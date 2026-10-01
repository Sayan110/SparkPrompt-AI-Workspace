/**
 * Phase 4F — UI login journey (frozen 4A semantics):
 *  1. valid credentials → HttpOnly session cookie, welcome toast, /dashboard;
 *  2. the auth pages cross-link (login ↔ signup) without a full reload.
 */
import { expect, test } from "../helpers/gate";
import { SESSION_COOKIE, TEST_PASSWORD, USER_A } from "../config";
import { gotoRoute, toast } from "../helpers/ui";

test("valid credentials sign in and land on the dashboard", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", { level: 1, name: "Welcome back" })).toBeVisible();

  await page.getByLabel("Email address").fill(USER_A.email);
  await page.locator('input[type="password"]').fill(TEST_PASSWORD);
  await page.getByRole("button", { name: "Enter SparkPrompt" }).click();

  await page.waitForURL("**/dashboard");
  await expect(toast(page, /^Welcome, .+\. Your workspace is ready\.$/)).toBeVisible();

  // Session is a server-set HttpOnly cookie — never browser storage.
  const cookies = await page.context().cookies();
  const session = cookies.find((cookie) => cookie.name === SESSION_COOKIE);
  expect(session, "session cookie after login").toBeTruthy();
  expect(session!.httpOnly).toBe(true);

  await gotoRoute(page, "/dashboard");
});

test("login and signup pages cross-link", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("link", { name: "Create a workspace" }).click();
  await page.waitForURL("**/signup");
  await expect(page.getByRole("heading", { level: 1, name: "Create your workspace" })).toBeVisible();

  await page.getByRole("link", { name: "Sign in" }).click();
  await page.waitForURL("**/login");
  await expect(page.getByRole("heading", { level: 1, name: "Welcome back" })).toBeVisible();
});
