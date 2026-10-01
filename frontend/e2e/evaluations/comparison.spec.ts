/**
 * Phase 4F — evaluation comparison from Evaluation History (Phase 3R supplied
 * mode), entirely without a live provider:
 *  - three real evaluations against ONE seeded fixture run;
 *  - rows arrive newest-first with server-computed scores (pinned below);
 *  - two rows whose evaluators differ in a semantic field compare as
 *    "Not comparable" with the concrete mismatch, while still reporting the
 *    output/metadata diff (same fixture output → Identical);
 *  - swapping in an evaluator with an identical rule structure compares as
 *    "Comparable" with criterion-by-criterion results.
 * The spec deletes its prompt afterwards so later absolute-count assertions
 * stay exact.
 */
import { expect, test } from "../helpers/gate";
import { DEV_API, STORAGE_A } from "../config";
import {
  createEvaluationViaApi,
  createPromptViaApi,
  seedPromptRun,
} from "../helpers/api-state";
import { expectToast } from "../helpers/ui";

test.use({ storageState: STORAGE_A });

const TITLE = "E2E comparison journey prompt";

test("history comparison: incompatible evaluators and identical evaluators", async ({
  page,
}) => {
  // WSL-side fixture seeding (seedPromptRun) needs more than the default 30s.
  test.setTimeout(60_000);
  const request = page.context().request;

  const prompt = await createPromptViaApi(request, {
    title: TITLE,
    idea: "E2E comparison journey idea.",
    body: "E2E comparison journey body.",
  });
  try {
    const runId = await seedPromptRun(prompt.id);
    // A and C share rule structure (comparable); B differs in `text`.
    await createEvaluationViaApi(request, runId, [
      { id: "rule-a", type: "contains", text: "Test" },
    ]);
    await createEvaluationViaApi(request, runId, [
      { id: "rule-b", type: "contains", text: "Absent" },
    ]);
    await createEvaluationViaApi(request, runId, [
      { id: "rule-c", type: "contains", text: "Test" },
    ]);

    await page.goto(`/studio?prompt=${prompt.id}`);
    await expect(
      page.getByRole("navigation", { name: "Main navigation" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", {
        level: 1,
        name: "Magic Studio",
        exact: true,
      }),
    ).toBeVisible();

    await page.getByRole("button", { name: "Build evaluation rules" }).click();

    // Newest-first order is pinned by the deterministic scores: C passes,
    // B fails (fixture lacks "Absent"), A passes.
    // Rows are located by the button's static `title` attribute, never by its
    // text: selecting a row changes the label to "Selected: left/right", and a
    // hasText filter would drop the held row from this dynamic locator (the
    // row would silently reindex under nth()).
    const rows = page
      .locator("li")
      .filter({ has: page.locator('button[title="Select for comparison"]') });
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0)).toContainText("Score 100%");
    await expect(rows.nth(1)).toContainText("Score 0%");
    await expect(rows.nth(2)).toContainText("Score 100%");
    await expect(rows.nth(1)).toContainText("FAIL");

    // First pick → left slot; one side asks for a second evaluation.
    await rows
      .nth(0)
      .getByRole("button", { name: "Select for comparison" })
      .click();
    await expect(
      rows.nth(0).getByRole("button", { name: "Selected: left" }),
    ).toBeVisible();
    await expect(
      page.getByText("Select another evaluation to compare."),
    ).toBeVisible();

    // Second pick → right slot; the compare action appears.
    await rows
      .nth(1)
      .getByRole("button", { name: "Select for comparison" })
      .click();
    await expect(rows.nth(1).getByRole("button", { name: "Selected: right" })).toBeVisible();
    await expect(page.getByText("Two evaluations selected.")).toBeVisible();

    // Phase 1: evaluators differ in a semantic field → not comparable, the
    // mismatch is named, and output/metadata diffs are still reported.
    await page.getByRole("button", { name: "Compare", exact: true }).click();
    await expectToast(
      page,
      "Comparison complete — the differences are shown below.",
    );
    await expect(page.getByText("Not comparable", { exact: true })).toBeVisible();
    await expect(
      page.getByText(
        "The two evaluators differ structurally, so criteria are not compared. Output, prompt, and metadata differences are still reported below.",
      ),
    ).toBeVisible();
    await expect(page.getByText("Rule 1 · text")).toBeVisible();
    await expect(page.getByText("Criteria", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Identical", { exact: true })).toBeVisible();

    // Swap the right slot: clicking the held row clears it…
    await rows
      .nth(1)
      .getByRole("button", { name: "Selected: right" })
      .click();
    await expect(
      page.getByText("Select another evaluation to compare."),
    ).toBeVisible();

    // …and the structurally identical third evaluation takes the slot.
    await rows
      .nth(2)
      .getByRole("button", { name: "Select for comparison" })
      .click();
    await expect(page.getByText("Two evaluations selected.")).toBeVisible();

    // Phase 2: identical evaluators → criterion-by-criterion comparison.
    await page.getByRole("button", { name: "Compare", exact: true }).click();
    await expectToast(
      page,
      "Comparison complete — the differences are shown below.",
    );
    await expect(page.getByText("Comparable", { exact: true })).toBeVisible();
    await expect(
      page.getByText(
        "Same evaluator on both sides — criteria are compared position by position.",
      ),
    ).toBeVisible();
    await expect(page.getByText("Criteria", { exact: true })).toBeVisible();
    await expect(page.getByText("Identical", { exact: true })).toBeVisible();
  } finally {
    // Leave no data behind: prompts/projects assert exact prompt counts.
    const cleanup = await request.delete(`${DEV_API}/api/prompts/${prompt.id}`);
    expect(
      cleanup.ok(),
      "owner delete of the comparison journey prompt",
    ).toBeTruthy();
  }
});
