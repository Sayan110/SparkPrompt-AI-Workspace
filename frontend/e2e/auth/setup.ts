/**
 * Phase 4F setup project — creates the deterministic E2E accounts A and B in
 * the disposable database through the real signup endpoint, then stores their
 * authenticated contexts (storageState) so the suite logs in exactly twice per
 * run instead of once per test (the login limiter is 10 / 60s per IP).
 */
import { test as setup, expect, type Browser } from "@playwright/test";

import { DEV_API, STORAGE_A, STORAGE_B, USER_A, USER_B } from "../config";
import { signupUser } from "../helpers/api-state";

async function seedAccount(browser: Browser, email: string, storagePath: string): Promise<void> {
  const context = await browser.newContext({ baseURL: DEV_API });
  try {
    await signupUser(context.request, email);
    const me = await context.request.get(`${DEV_API}/api/auth/me`);
    expect(me.ok(), `${email} session after signup`).toBeTruthy();
    await context.storageState({ path: storagePath });
  } finally {
    await context.close();
  }
}

setup("seed deterministic E2E accounts A and B", async ({ browser }) => {
  await seedAccount(browser, USER_A.email, STORAGE_A);
  await seedAccount(browser, USER_B.email, STORAGE_B);
});
