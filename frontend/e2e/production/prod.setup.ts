/**
 * Phase 4F production setup — seeds the deterministic production-proxy account
 * through the REAL nginx proxy (DEPLOYMENT.md §11a stack on :8080) and stores
 * its authenticated context (storageState) for the `production` project.
 *
 * Runs only under `npm run test:e2e:prod` (E2E_SKIP_DEV_STACK=1); the stack
 * itself is brought up and torn down by the operator per §11a — this file
 * neither starts nor stops it. The account lives in the disposable
 * sparkprompt_prod_smoke database, never the persistent one.
 */
import { test as setup, expect, type Browser } from "@playwright/test";

import { PROD_BASE, PROD_USER, STORAGE_PROD } from "../config";
import { signupUser } from "../helpers/api-state";

setup("seed the deterministic production-proxy account", async ({ browser }) => {
  const context = await browser.newContext({ baseURL: PROD_BASE });
  try {
    // Signup (or login fallback on rerun) through the proxy — one proxied
    // auth round trip before any browser test runs.
    await signupUser(context.request, PROD_USER.email, PROD_BASE);
    const me = await context.request.get(`${PROD_BASE}/api/auth/me`);
    expect(me.ok(), `${PROD_USER.email} session through the proxy`).toBeTruthy();
    await context.storageState({ path: STORAGE_PROD });
  } finally {
    await context.close();
  }
});
