/**
 * Phase 4F — CSP enforcement (after the evidence pass).
 *
 * Evidence (first run): the document shipped no CSP — neither a
 * Content-Security-Policy header nor a <meta http-equiv> tag — so the 4F
 * decision was to ENFORCE a policy via next.config.ts headers() (the nginx
 * proxy passes CSP through untouched and never duplicates it). This spec
 * verifies the shipped policy carries the hard directives, targets the
 * disposable E2E API in connect-src, and never permits eval.
 */
import { expect, test } from "../helpers/gate";

const META_CSP = 'meta[http-equiv="Content-Security-Policy"]';

test("the enforced CSP ships on the document and forbids eval", async ({
  page,
}) => {
  const response = await page.goto("/login");
  expect(response?.status(), "login document status").toBe(200);

  const headerCsp = response?.headers()["content-security-policy"] ?? "";
  const metaCount = await page.locator(META_CSP).count();
  const metaCsp =
    metaCount > 0
      ? await page.locator(META_CSP).first().getAttribute("content")
      : null;

  // Enforcement lives in the response header (next.config.ts headers()).
  expect(headerCsp, "CSP ships as a response header").toBeTruthy();
  expect(metaCsp, "no redundant meta CSP (single source of truth)").toBeNull();

  const policy = headerCsp;
  // Hard directives: clickjacking, plugin injection, base hijack, hijack forms.
  expect(policy).toContain("frame-ancestors 'none'");
  expect(policy).toContain("object-src 'none'");
  expect(policy).toContain("base-uri 'self'");
  expect(policy).toContain("form-action 'self'");
  expect(policy).toContain("default-src 'self'");
  // The browser may only talk to this origin and the disposable API — the
  // dev-default :8000 fallback is NOT baked in (NEXT_PUBLIC_API_URL is set).
  expect(policy).toContain("connect-src 'self' http://localhost:8100");
  expect(policy).not.toContain("localhost:8000");
  // Real runtime dependency: app/globals.css @imports Google Fonts and the
  // CSS pulls woff2 from fonts.gstatic.com — named origins, not https:.
  expect(policy).toContain("https://fonts.googleapis.com");
  expect(policy).toContain("https://fonts.gstatic.com");
  // Evidence rule (holds before and after enforcement): never eval.
  expect(policy).not.toContain("unsafe-eval");
});
