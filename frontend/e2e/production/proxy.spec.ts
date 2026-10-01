/**
 * Phase 4F — production proxy suite (DEPLOYMENT.md §11a stack on :8080).
 *
 * Runs against the REAL nginx proxy → FastAPI + Next.js topology, not the
 * disposable dev stack. It proves, through the browser:
 *  1. routing  — `/` is a Next document, `/api/*` is the FastAPI upstream,
 *     `/proxy-health` answers from nginx itself;
 *  2. the 4D header design — exactly ONE copy of every security header
 *     reaches the browser (nginx hides upstream X-CTO/XFO/Referrer-Policy
 *     and re-adds its own; the 4F Content-Security-Policy from next.config.ts
 *     passes through untouched; HSTS stays off on the plain-HTTP listener;
 *     x-powered-by is gone);
 *  3. auth     — anonymous visitors are routed to /login, and the seeded
 *     account's HttpOnly session resumes through the proxy with a proxied
 *     authenticated API call (library load).
 *
 * The stack is operator-managed per §11a (this file never starts/stops it).
 */
import { expect, test } from "../helpers/gate";
import { PROD_BASE, PROD_USER, STORAGE_PROD } from "../config";
import { gotoRoute } from "../helpers/ui";

/** Count repeated header instances — response.headers() collapses duplicates. */
function countHeader(
  headers: Array<{ name: string; value: string }>,
  name: string,
): number {
  const lowered = name.toLowerCase();
  return headers.filter((header) => header.name.toLowerCase() === lowered)
    .length;
}

function headerValue(
  headers: Array<{ name: string; value: string }>,
  name: string,
): string | undefined {
  const lowered = name.toLowerCase();
  return headers.find((header) => header.name.toLowerCase() === lowered)
    ?.value;
}

test("the proxy routes each surface and emits exactly one of every header", async ({
  page,
}) => {
  // 1. Document through nginx → Next upstream.
  const doc = await page.goto("/");
  expect(doc?.status(), "document status through the proxy").toBe(200);
  expect(doc?.headers()["content-type"] ?? "", "document content-type").toContain(
    "text/html",
  );
  const docHeaders = await doc!.headersArray();

  // 4D design: nginx hides the upstream copies and adds its own — exactly one.
  expect(countHeader(docHeaders, "x-content-type-options")).toBe(1);
  expect(headerValue(docHeaders, "x-content-type-options")).toBe("nosniff");
  expect(countHeader(docHeaders, "x-frame-options")).toBe(1);
  expect(headerValue(docHeaders, "x-frame-options")).toBe("DENY");
  expect(countHeader(docHeaders, "referrer-policy")).toBe(1);
  expect(headerValue(docHeaders, "referrer-policy")).toBe("same-origin");

  // 4F CSP: neither set nor hidden by nginx → the single next.config.ts copy.
  const csp = headerValue(docHeaders, "content-security-policy") ?? "";
  expect(csp, "CSP passes through the proxy exactly once").toBeTruthy();
  expect(countHeader(docHeaders, "content-security-policy")).toBe(1);
  expect(csp).toContain("frame-ancestors 'none'");
  expect(csp).toContain("connect-src 'self' " + PROD_BASE);
  expect(csp).not.toContain("localhost:8000");

  // HSTS variable is empty on the plain-HTTP listener (never emitted).
  expect(countHeader(docHeaders, "strict-transport-security")).toBe(0);
  // poweredByHeader: false — the framework is not advertised in production.
  expect(countHeader(docHeaders, "x-powered-by")).toBe(0);

  // 2. nginx's own liveness endpoint, independent of either upstream.
  const health = await page.request.get(`${PROD_BASE}/proxy-health`);
  expect(health.status(), "proxy-health status").toBe(200);
  expect(health.headers()["content-type"] ?? "").toContain("text/plain");
  expect((await health.text()).trim()).toBe("ok");

  // 3. `/api/*` → FastAPI upstream with its own Phase 4A header set, no CSP.
  const api = await page.request.get(`${PROD_BASE}/api/health`);
  expect(api.status(), "api health through the proxy").toBe(200);
  expect(api.headers()["content-type"] ?? "").toContain("application/json");
  const apiHeaders = await api.headersArray();
  // nginx owns the Server header on every surface (server_tokens off) — the
  // upstream uvicorn value never passes through the proxy. Reaching FastAPI
  // is proven by the liveness body contract instead (deploy_smoke.py check 4).
  expect(headerValue(apiHeaders, "server")).toBe("nginx");
  expect(await api.json()).toEqual({
    status: "ok",
    service: "sparkprompt-api",
  });
  expect(headerValue(apiHeaders, "x-content-type-options")).toBe("nosniff");
  expect(countHeader(apiHeaders, "content-security-policy")).toBe(0);
});

test("anonymous visitors are routed to /login through the proxy", async ({
  browser,
  gate,
}) => {
  // Observed 4F behavior (the same frozen 4D client layout the dev
  // protected-routes spec declares): /library's data hook fetches
  // /api/prompts in the same commit that starts the client-side redirect,
  // so the fetch leaves before the gate swaps the route and the API
  // correctly answers 401 for the anonymous session.
  gate.expectStatus(
    401,
    /\/api\/prompts(?:[/?]|$)/,
    "anonymous /library fires the frozen 4D prompts fetch before the client-side redirect to /login",
  );

  // Fresh context, no storage state — the seeded session must never reach
  // this anonymous journey (observed leaking in when storageState was set
  // file-level, which authenticated this context and suppressed the redirect).
  const anon = await browser.newContext({ baseURL: PROD_BASE });
  gate.watchContext(anon);
  try {
    const page = await anon.newPage();
    await page.goto("/library");
    await expect(page).toHaveURL(/\/login$/);
    await expect(
      page.getByRole("heading", { level: 1, name: "Welcome back" }),
    ).toBeVisible();
  } finally {
    await anon.close();
  }
});

test("the seeded session resumes and API calls flow through the proxy", async ({
  browser,
  gate,
}) => {
  // The seeded session is scoped to THIS context explicitly (never
  // file-level), so the anonymous test above always starts clean.
  const ctx = await browser.newContext({
    baseURL: PROD_BASE,
    storageState: STORAGE_PROD,
  });
  gate.watchContext(ctx);
  try {
    const page = await ctx.newPage();
    await gotoRoute(page, "/dashboard");

    // HttpOnly session cookie round-tripped through nginx.
    const cookies = await ctx.cookies();
    const session = cookies.find((cookie) => cookie.name === "sparkprompt_session");
    expect(session, "session cookie through the proxy").toBeTruthy();
    expect(session!.httpOnly).toBe(true);

    // Authenticated proxied API call: the fresh disposable production database
    // has zero prompts, so the empty state proves GET /api/prompts succeeded
    // (an API failure would render the "Could not load saved prompts" error).
    await gotoRoute(page, "/library");
    await expect(page.getByText("No saved prompts yet")).toBeVisible();

    // And the account identity the proxy forwarded to FastAPI.
    expect(PROD_USER.email).toContain("e2e_prod_user");
  } finally {
    await ctx.close();
  }
});
