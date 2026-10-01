/**
 * Phase 4F — security header checks (enforcement pass).
 *
 * The evidence pass (recorded in frontend/e2e/README.md) found the document
 * shipped none of these headers; enforcement now lives in next.config.ts
 * headers(). Assertions verify the enforced set on the HTML document served
 * directly by Next (:3000 — the topology this suite exercises), plus the
 * Phase 4A header set the FastAPI side already ships, and the CORS contract
 * proven by every suite fetch. The evidence JSON is still attached so the
 * 4F report keeps the raw headers.
 */
import { expect, test } from "../helpers/gate";
import { DEV_API } from "../config";

test("document and API ship the enforced security header set", async (
  { page },
  testInfo,
) => {
  const doc = await page.goto("/login");
  expect(doc?.status(), "login document status").toBe(200);
  const headers = doc?.headers() ?? {};
  expect(headers["content-type"] ?? "", "document content-type").toContain(
    "text/html",
  );

  // Enforced by next.config.ts headers() — evidence pass: all were absent.
  expect(
    headers["content-security-policy"],
    "Content-Security-Policy ships on the document",
  ).toBeTruthy();
  expect(
    headers["x-content-type-options"],
    "X-Content-Type-Options: nosniff",
  ).toBe("nosniff");
  expect(headers["x-frame-options"], "X-Frame-Options: DENY").toBe("DENY");
  expect(headers["referrer-policy"], "Referrer-Policy: same-origin").toBe(
    "same-origin",
  );
  // poweredByHeader: false — no framework disclosure.
  expect(
    headers["x-powered-by"],
    "x-powered-by suppressed (poweredByHeader: false)",
  ).toBeUndefined();

  // Phase 4A API header set (already shipped — asserted so a regression on
  // the FastAPI side fails here too) + CORS for the web origin.
  const api = await page.request.get(`${DEV_API}/api/health`, {
    headers: { Origin: "http://localhost:3000" },
  });
  expect(api.ok(), "health answers the web origin").toBeTruthy();
  const apiHeaders = api.headers();
  expect(apiHeaders["x-content-type-options"], "API nosniff").toBe("nosniff");
  expect(apiHeaders["x-frame-options"], "API XFO").toBe("DENY");
  expect(apiHeaders["referrer-policy"], "API referrer policy").toBe(
    "same-origin",
  );
  expect(
    apiHeaders["access-control-allow-origin"],
    "API allows the web origin (implied by every successful suite fetch)",
  ).toBeTruthy();

  await testInfo.attach("security-headers-evidence.json", {
    body: JSON.stringify({ document: headers, api: apiHeaders }, null, 2),
    contentType: "application/json",
  });
  console.log(`[4F-security-evidence] ${JSON.stringify({ document: headers, api: apiHeaders })}`);
});
