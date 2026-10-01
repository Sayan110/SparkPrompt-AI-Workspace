/**
 * Phase 4F — browser-visible error states:
 *  1. an unknown URL renders the framework's 404 page (no crash, no redirect);
 *  2. a failing prompts fetch renders the Library's ErrorState, and "Try again"
 *     recovers once the endpoint answers again (the deliberate 5xx is declared
 *     per test — the gate never auto-allows 5xx).
 */
import { expect, test } from "../helpers/gate";
import { STORAGE_A } from "../config";

test.use({ storageState: STORAGE_A });

test("an unknown route renders the 404 page", async ({ page, gate }) => {
  // The document request itself is a deliberate HTTP 404 — that IS the product
  // behavior under test (Next serves its not-found page with a 404 status).
  gate.expectStatus(
    404,
    /\/this-route-does-not-exist-4f$/,
    "navigating to an unknown URL intentionally serves the product 404 page with HTTP status 404",
  );

  await page.goto("/this-route-does-not-exist-4f");
  await expect(page.getByText("This page could not be found.")).toBeVisible();
  await expect(page).toHaveURL(/this-route-does-not-exist-4f/);
});

test("a failing prompts fetch shows the error state and Try again recovers", async ({
  page,
  gate,
}) => {
  gate.expectStatus(
    500,
    /\/api\/prompts(?:[/?]|$)/,
    "deliberate one-shot 500 injected with page.route to exercise the Library error state",
  );

  let failNext = true;
  await page.route("**/api/prompts**", (route) => {
    if (failNext) {
      failNext = false;
      return route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Injected once for the 4F error journey." }),
      });
    }
    return route.continue();
  });

  await page.goto("/library");
  await expect(page.getByText("Could not load saved prompts")).toBeVisible();

  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByText("Could not load saved prompts")).toHaveCount(0);
  // Recovery lands on the real endpoint: user A's library is empty at this
  // point in the serial run (errors/ runs before prompts/, which seeds data).
  await expect(page.getByText("No saved prompts yet")).toBeVisible();
});
