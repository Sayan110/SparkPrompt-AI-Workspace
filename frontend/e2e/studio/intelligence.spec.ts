/**
 * Phase 4F — AI boundary tested WITHOUT a live provider (evidence, not a fake):
 *  - "Generate prompt" works because it is the local enhancer (provider-free);
 *  - the AI panel is honestly gated: provider/model selects disabled with
 *    "No provider available", "Enhance with AI" disabled even with content,
 *    and the status line reports zero availability either way it is phrased.
 * No provider is configured in the disposable stack — this spec proves the
 * product does not pretend otherwise.
 */
import { expect, test } from "../helpers/gate";
import { STORAGE_A } from "../config";
import { gotoRoute } from "../helpers/ui";

test.use({ storageState: STORAGE_A });

test("the AI panel stays honestly gated while local generation works", async ({
  page,
}) => {
  await gotoRoute(page, "/studio");

  await page.getByPlaceholder(/Example: Build a landing page/).fill(
    "E2E AI-boundary check: draft a friendly bug triage checklist.",
  );

  // Local generation is available…
  const generate = page.getByRole("button", { name: "Generate prompt" });
  await expect(generate).toBeEnabled();
  await generate.click();
  await expect(page.locator("pre").first()).toBeVisible();

  // …while the AI provider surface is disabled with zero providers configured.
  const providerSelect = page.getByLabel("AI provider");
  await expect(providerSelect).toBeDisabled();
  await expect(providerSelect).toHaveValue("");
  // Option visibility is unreliable in Chromium — assert the select's own text.
  await expect(providerSelect).toContainText("No provider available");

  const modelSelect = page.getByLabel("AI model");
  await expect(modelSelect).toBeDisabled();

  // Disabled even now that there is generated content to enhance.
  await expect(page.getByRole("button", { name: "Enhance with AI" })).toBeDisabled();

  // The status line reports zero availability in the product's own wording
  // (both phrasings are honest server-driven states).
  await expect(
    page.getByText(/No AI providers reported yet|0 of \d+ providers available/),
  ).toBeVisible();
});
