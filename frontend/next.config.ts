import type { NextConfig } from "next";

/**
 * Phase 4F — security headers (evidence-first enforcement).
 *
 * Evidence pass (e2e/security/*.spec.ts, run recorded in frontend/e2e/README.md):
 *   - the HTML document shipped NO Content-Security-Policy (header or meta),
 *     no X-Content-Type-Options, no X-Frame-Options, no Referrer-Policy, and
 *     an `x-powered-by: Next.js` framework disclosure;
 *   - the FastAPI side already shipped the Phase 4A header set (nosniff,
 *     DENY, same-origin) — untouched here;
 *   - the 4D nginx proxy hides upstream X-CTO/XFO/Referrer-Policy and
 *     re-adds its own deterministic set (locations.conf), and it neither
 *     sets nor hides Content-Security-Policy — so the policy below reaches
 *     the browser exactly once in every topology (direct and proxied).
 *
 * Decisions:
 *   - CSP enforced here (the only surface that can ship it — nothing else
 *     in the stack sets it). frame-ancestors/object-src/base-uri/form-action
 *     carry the hard security value; script/style keep 'unsafe-inline'
 *     because Next.js RSC flight data ships as inline <script> tags and
 *     component styles arrive inline — nonce-based script hardening is a
 *     deliberate DEFER (documented in frontend/e2e/README.md).
 *   - 'unsafe-eval' is never permitted (asserted by e2e/security/csp.spec.ts).
 *   - connect-src is exact: the build-time API origin when present (production
 *     requires NEXT_PUBLIC_API_URL — infra/docker-compose.prod.yml), else the
 *     documented localhost dev default from lib/utils.ts apiUrl(); ws:/wss:
 *     keep the dev-server HMR socket working under the same policy.
 */

const apiOrigin = process.env.NEXT_PUBLIC_API_URL;
const connectSrc = [
  "'self'",
  ...(apiOrigin
    ? [apiOrigin]
    : ["http://localhost:8000", "http://127.0.0.1:8000"]),
  "ws:",
  "wss:",
].join(" ");

const contentSecurityPolicy = [
  "default-src 'self'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "object-src 'none'",
  "script-src 'self' 'unsafe-inline'",
  // app/globals.css @imports the Google Fonts stylesheet at runtime and the
  // CSS then pulls woff2 files from fonts.gstatic.com — both origins are a
  // real product dependency (enforcement run proved it when they were
  // missing), so they are named explicitly instead of widening to https:.
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "img-src 'self' data: blob:",
  "font-src 'self' data: https://fonts.gstatic.com",
  `connect-src ${connectSrc}`,
].join("; ");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Phase 4F: stop advertising the framework on every response.
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: contentSecurityPolicy },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
