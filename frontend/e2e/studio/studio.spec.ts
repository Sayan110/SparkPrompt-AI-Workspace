/**
 * Phase 4F — providerless Studio authoring (no AI provider configured):
 *  1. the local enhancer produces a full prompt with zero providers;
 *  2. the generated body is display-only (<pre>), never an editable field;
 *  3. Rework cycles depth 2 → 3 and wraps 3 → 1 (server-independent toasts);
 *  4. Clear resets input and output to the empty state.
 */
import { expect, test } from "../helpers/gate";
import { STORAGE_A } from "../config";
import { gotoRoute, toast } from "../helpers/ui";

test.use({ storageState: STORAGE_A });

const IDEA =
  "E2E studio authoring: summarize a pull request for a release manager in plain text.";

test("local enhancer, display-only body, depth cycling, and clear", async ({ page }) => {
  await gotoRoute(page, "/studio");

  const depthInput = page.getByLabel("Prompt depth");
  await expect(depthInput).toHaveValue("2"); // default: Balanced

  await page.getByPlaceholder(/Example: Build a landing page/).fill(IDEA);
  await page.getByRole("button", { name: "Generate prompt" }).click();

  // Generated body renders display-only: a <pre>, with no editable surface.
  const output = page.locator("pre").first();
  await expect(output).toBeVisible();
  expect((await output.innerText()).length).toBeGreaterThan(150);
  await expect(page.locator('[contenteditable="true"]')).toHaveCount(0);

  // Rework advances the depth and reports which depth it used:
  // 2 (Balanced) → 3 (Deep dive) → wrap → 1 (Quick).
  await page.getByRole("button", { name: "Rework" }).click();
  await expect(toast(page, "Reworked with deep dive depth.")).toBeVisible();
  await expect(depthInput).toHaveValue("3");

  await page.getByRole("button", { name: "Rework" }).click();
  await expect(toast(page, "Reworked with quick depth.")).toBeVisible();
  await expect(depthInput).toHaveValue("1");

  // Clear empties both the idea and the output (empty state returns).
  await page.locator('button[title="Clear input and output"]').click();
  await expect(page.getByPlaceholder(/Example: Build a landing page/)).toHaveValue("");
  await expect(page.getByText("A little magic awaits")).toBeVisible();
  await expect(page.locator("pre")).toHaveCount(0);
});
