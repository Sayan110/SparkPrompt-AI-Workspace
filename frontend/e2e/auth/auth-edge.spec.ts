/**
 * Phase 4F — auth edge cases (frozen 4A semantics, observed only):
 *  1. invalid credentials → server-truth 401 message in the alert role;
 *  2. duplicate signup → 409 message;
 *  3. short password → native constraint validation blocks the request entirely;
 *  4. API unreachable (offline) → the status-0 message, no crash, no navigation.
 */
import { expect, test } from "../helpers/gate";
import { TEST_PASSWORD, USER_A } from "../config";
import { toast } from "../helpers/ui";

test("wrong password shows the 401 error and stays on the login page", async ({
  page,
  gate,
}) => {
  gate.expectStatus(401, /\/api\/auth\/login$/, "deliberate invalid-credentials login");

  await page.goto("/login");
  await page.getByLabel("Email address").fill(USER_A.email);
  await page.locator('input[type="password"]').fill("WrongPassword!9");
  await page.getByRole("button", { name: "Enter SparkPrompt" }).click();

  // Scoped to the form's <p role="alert">: Next.js ships a route-announcer
  // <div role="alert"> that is always in the DOM and breaks strict mode.
  await expect(page.locator('p[role="alert"]')).toHaveText("Invalid email or password.");
  await expect(page).toHaveURL(/\/login/);
});

test("duplicate signup surfaces the 409 message inline", async ({ page, gate }) => {
  gate.expectStatus(409, /\/api\/auth\/signup$/, "deliberate duplicate-account signup");

  await page.goto("/signup");
  await page.getByLabel("Email address").fill(USER_A.email);
  await page.locator('input[type="password"]').fill(TEST_PASSWORD);
  await page.getByRole("button", { name: "Create workspace" }).click();

  await expect(page.locator('p[role="alert"]')).toHaveText(
    "An account with this email already exists.",
  );
  await expect(page).toHaveURL(/\/signup/);
});

test("a short password is blocked before any network request", async ({ page }) => {
  let signupRequests = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("/api/auth/signup")) {
      signupRequests += 1;
    }
  });

  await page.goto("/signup");
  await page.getByLabel("Email address").fill("e2e_short_password@example.com");
  await page.locator('input[type="password"]').fill("short7c"); // 7 chars < minLength 8
  await page.getByRole("button", { name: "Create workspace" }).click();

  // Native constraint validation (form minLength) blocks submit entirely.
  await expect(page).toHaveURL(/\/signup/);
  expect(signupRequests).toBe(0);
  await expect(page.locator('p[role="alert"]')).toHaveCount(0);
});

test("offline submit shows the API-unreachable message", async ({ page, gate }) => {
  // Offline is total by design, so any disconnected failure in this window is
  // the behavior under test (login fetch plus any concurrent route prefetch).
  gate.expectRequestFailure(
    /net::ERR_INTERNET_DISCONNECTED/,
    undefined,
    "context forced offline for the API-unreachable journey",
  );
  gate.expectConsoleError(
    /net::ERR_INTERNET_DISCONNECTED/,
    undefined,
    "console mirror of the offline fetches",
  );

  await page.goto("/login");
  await page.getByLabel("Email address").fill(USER_A.email);
  await page.locator('input[type="password"]').fill(TEST_PASSWORD);

  await page.context().setOffline(true);
  try {
    await page.getByRole("button", { name: "Enter SparkPrompt" }).click();
    await expect(page.locator('p[role="alert"]')).toHaveText(
      "The API is not reachable. Start FastAPI on port 8000.",
    );
    await expect(page).toHaveURL(/\/login/);
    await expect(toast(page, /Welcome/)).toHaveCount(0);
  } finally {
    await page.context().setOffline(false);
  }
});
