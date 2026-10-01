/**
 * Phase 4F — prompts lifecycle (deterministic fixture, no AI provider):
 * generate (the app's own offline local enhancer) → save v1 → rework + save
 * versions → restore from history (append-only) → Library discovery → search
 * → delete. Generation here makes no gateway call; the server has zero
 * available providers, so this journey proves the providerless authoring path.
 */
import { expect, test } from "../helpers/gate";
import { STORAGE_A } from "../config";
import { listPromptsViaApi } from "../helpers/api-state";
import { expectToast, gotoRoute } from "../helpers/ui";

test.use({ storageState: STORAGE_A });

const IDEA =
  "E2E lifecycle journey for Phase 4F: draft a concise release note announcing deterministic browser validation, addressed to release managers, delivered as plain text.";

test("generate → save → rework versions → restore → library → delete", async ({
  page,
}) => {
  await gotoRoute(page, "/studio");

  await page.getByPlaceholder(/Example: Build a landing page/).fill(IDEA);
  await page.getByRole("button", { name: "Generate prompt" }).click();

  // The local enhancer produced a prompt body with no provider configured.
  await expect(page.getByText("A little magic awaits")).toHaveCount(0);

  // One save button whose label alternates Save / Save version with state.
  const saveButton = page.getByRole("button", { name: /^Save( version)?$/ });

  // v1 — server truth: creating a prompt with a body returns version_number 1,
  // so the Studio reports the server-sourced number on the very first save.
  await saveButton.click();
  await expectToast(page, "Saved as version 1.");
  await expect(page.getByText(/Version history · 1/)).toBeVisible();

  // v2 — rework is a deterministic local regeneration at a different depth.
  await page.getByRole("button", { name: "Rework" }).click();
  await expectToast(page, /Reworked with .* depth\./);
  await saveButton.click();
  await expectToast(page, "Saved as version 2.");
  await expect(page.getByText(/Version history · 2/)).toBeVisible();

  // v3
  await page.getByRole("button", { name: "Rework" }).click();
  await saveButton.click();
  await expectToast(page, "Saved as version 3.");
  await expect(page.getByText(/Version history · 3/)).toBeVisible();

  await expect(page.getByText("v1", { exact: true })).toBeVisible();
  await expect(page.getByText("v2", { exact: true })).toBeVisible();
  await expect(page.getByText("v3", { exact: true })).toBeVisible();

  // Restore v1 → appends v4; nothing existing is overwritten (frozen 3I).
  await page.locator('button[title="Restore v1 as a new version"]').click();
  const restoreDialog = page.getByRole("dialog");
  await expect(restoreDialog.getByText("Restore version 1?")).toBeVisible();
  await restoreDialog.getByRole("button", { name: "Restore", exact: true }).click();
  await expectToast(page, "Restored as version 4.");
  await expect(page.getByText(/Version history · 4/)).toBeVisible();
  await expect(page.getByText("v4", { exact: true })).toBeVisible();
  await expect(
    page.locator("li").filter({ hasText: "v4" }).getByText("Current", { exact: true }),
  ).toBeVisible();

  // Library discovery — the title is server truth from the API, never guessed.
  const prompts = await listPromptsViaApi(page.context().request);
  const created = prompts.find((item) => item.idea === IDEA);
  expect(created, "the saved prompt is listed by the API").toBeTruthy();
  if (!created) throw new Error("saved prompt missing from the API list");

  await gotoRoute(page, "/library");
  const row = page.locator("article").filter({ hasText: created.title });
  await expect(row).toHaveCount(1);

  // Search filters the library (and the miss state is honest).
  const search = page.getByLabel("Search saved prompts");
  await search.fill(created.title);
  await expect(row).toHaveCount(1);
  await search.fill("zzz-no-match-4f");
  await expect(page.getByText(/No prompts match/)).toBeVisible();
  await search.fill("");

  // Delete through the row's server-truth aria-label.
  await row.getByRole("button", { name: `Delete ${created.title}` }).click();
  const deleteDialog = page.getByRole("dialog");
  await expect(deleteDialog.getByText("Delete prompt?")).toBeVisible();
  await deleteDialog.getByRole("button", { name: "Delete", exact: true }).click();
  await expectToast(page, "Prompt deleted.");
  await expect(
    page.locator("article").filter({ hasText: created.title }),
  ).toHaveCount(0);
});
