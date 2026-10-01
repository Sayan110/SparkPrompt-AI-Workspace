/**
 * Phase 4F — sign-out journey: server revokes the session, the cookie is
 * removed, and protected routes stop serving the workspace afterwards.
 *
 * Frozen 4A semantics matter here: logout REVOKES the presented token
 * server-side (process-local denylist), so this test signs in first through
 * the real login endpoint to mint a session of its own. Presenting the shared
 * storageState token would poison it for every later spec in the run.
 */
import { expect, test } from "../helpers/gate";
import { DEV_API, SESSION_COOKIE, TEST_PASSWORD, USER_A } from "../config";
import { gotoRoute } from "../helpers/ui";

test("sign out clears the session and locks protected routes", async ({
  page,
  gate,
}) => {
  // Observed 4F behavior (frozen 4D layout): after sign-out the layout's data
  // hooks fire once without a session before the redirect completes, so the
  // API correctly answers 401 for the prompts list.
  gate.expectStatus(
    401,
    /\/api\/prompts(?:[/?]|$)/,
    "post-logout the layout fires its data hooks once with no session before redirecting",
  );

  // Own session for this test only (see header comment).
  const login = await page.context().request.post(`${DEV_API}/api/auth/login`, {
    data: { email: USER_A.email, password: TEST_PASSWORD },
  });
  expect(login.status(), "pre-logout login mints a fresh session").toBe(200);

  await gotoRoute(page, "/dashboard");

  await page.getByRole("button", { name: "Sign out" }).click();
  await page.waitForURL("**/login");
  await expect(
    page.getByRole("heading", { level: 1, name: "Welcome back" }),
  ).toBeVisible();

  const cookies = await page.context().cookies();
  expect(cookies.map((cookie) => cookie.name)).not.toContain(SESSION_COOKIE);

  // The session is really gone server-side: a protected route no longer renders
  // the shell (AppShell boots, sees no user, redirects to /login).
  await page.goto("/library");
  await page.waitForURL("**/login");
  await expect(
    page.getByRole("heading", { level: 1, name: "Welcome back" }),
  ).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Main navigation" }),
  ).toHaveCount(0);
});
