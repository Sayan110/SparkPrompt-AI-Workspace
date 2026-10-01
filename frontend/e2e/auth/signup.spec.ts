/**
 * Phase 4F — UI signup journey (frozen 4A semantics, USER_C is dedicated to
 * this journey; the signup limiter is 5 / 300s and the API restarts fresh
 * per run, so budget is setup(2) + duplicate-edge(1) + this(1) = 4):
 *  1. a brand-new account signs up through the real form → HttpOnly cookie,
 *     welcome toast, /dashboard;
 *  2. the password visibility toggle switches the field type without
 *     exposing the value in the DOM when hidden again.
 */
import { expect, test } from "../helpers/gate";
import { SESSION_COOKIE, TEST_PASSWORD, USER_C } from "../config";
import { gotoRoute, toast } from "../helpers/ui";

test("a new account signs up through the form and lands on the dashboard", async ({
  page,
}) => {
  await page.goto("/signup");
  await expect(
    page.getByRole("heading", { level: 1, name: "Create your workspace" }),
  ).toBeVisible();

  await page.getByLabel("Email address").fill(USER_C.email);
  const password = page.locator('input[type="password"]');
  await password.fill(TEST_PASSWORD);

  // Visibility toggle: hidden → shown → hidden again (value preserved).
  await page.getByRole("button", { name: "Show password" }).click();
  await expect(page.locator('input[name="password"][type="text"]')).toBeVisible();
  await page.getByRole("button", { name: "Hide password" }).click();
  await expect(page.locator('input[name="password"][type="password"]')).toBeVisible();
  await expect(page.locator('input[name="password"]')).toHaveValue(TEST_PASSWORD);

  await page.getByRole("button", { name: "Create workspace" }).click();

  await page.waitForURL("**/dashboard");
  await expect(toast(page, /^Welcome, .+\. Your workspace is ready\.$/)).toBeVisible();

  const cookies = await page.context().cookies();
  const session = cookies.find((cookie) => cookie.name === SESSION_COOKIE);
  expect(session, "session cookie after signup").toBeTruthy();
  expect(session!.httpOnly).toBe(true);

  await gotoRoute(page, "/dashboard");
});
