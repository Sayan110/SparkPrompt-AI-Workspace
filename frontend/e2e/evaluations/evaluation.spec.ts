/**
 * Phase 4F — evaluation journey WITHOUT a live provider (MODE A):
 *  - unattached Studio states the save requirement for history and keeps
 *    "Run evaluation" disabled — the honest AI boundary, no fake provider;
 *  - a real run-anchored evaluation (seeded fixture run + real
 *    POST /api/evaluations/run) flows into server-truth Evaluation history;
 *  - the row's detail view shows the immutable verdicts, run anchor, version,
 *    score, and read-only marker exactly as persisted.
 * State is created through API + ORM fixtures only; the spec deletes its
 * prompt afterwards so later absolute-count assertions stay exact.
 */
import { expect, test } from "../helpers/gate";
import { DEV_API, STORAGE_A } from "../config";
import {
  createEvaluationViaApi,
  createPromptViaApi,
  seedPromptRun,
} from "../helpers/api-state";
import { gotoRoute } from "../helpers/ui";

test.use({ storageState: STORAGE_A });

const TITLE = "E2E evaluation journey prompt";

test("evaluation history and detail are server truth; Run evaluation is gated", async ({
  page,
}) => {
  // WSL-side fixture seeding (seedPromptRun) needs more than the default 30s.
  test.setTimeout(60_000);
  const request = page.context().request;

  // Unattached Studio: history explains what it needs, and without a provider
  // or a captured test run the Run evaluation action stays disabled.
  await gotoRoute(page, "/studio");
  await page.getByRole("button", { name: "Build evaluation rules" }).click();
  await expect(
    page.getByText(
      "Save the prompt first — only evaluations anchored to a stored run are kept.",
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Run evaluation", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByText(
      "Evaluates the output against your rules — deterministic only, up to 20 rules.",
    ),
  ).toBeVisible();

  // Real prompt + deterministic fixture run, then attach via ?prompt=.
  const prompt = await createPromptViaApi(request, {
    title: TITLE,
    idea: "E2E evaluation journey idea.",
    body: "E2E evaluation journey body.",
  });
  try {
    const runId = await seedPromptRun(prompt.id);

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

    // Empty history first — server truth, not optimistic state.
    await page.getByRole("button", { name: "Build evaluation rules" }).click();
    await expect(page.getByText("Evaluation history · 0")).toBeVisible();
    await expect(page.getByText("No saved evaluations yet.")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Run evaluation", exact: true }),
    ).toBeDisabled();

    // One real evaluation: contains "Test" against the fixture output → PASS.
    await createEvaluationViaApi(request, runId, [
      {
        id: "fixture-contains",
        label: "Fixture output contains Test",
        type: "contains",
        text: "Test",
      },
    ]);

    await page.getByRole("button", { name: "Refresh" }).click();
    await expect(page.getByText("Evaluation history · 1")).toBeVisible();

    const row = page.locator("li").filter({ hasText: "Select for comparison" });
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("PASS");
    await expect(row).toContainText("Score 100%");
    await expect(row).toContainText("Equal criteria");
    await expect(row).toContainText("v1");
    await expect(
      row.getByRole("button", { name: "Select for comparison" }),
    ).toBeVisible();

    // Detail: immutable snapshots — run anchor, version, verdict label as
    // stored, and the read-only marker.
    await row.getByRole("button", { name: "View", exact: true }).click();
    await expect(row.getByText(/· run [0-9a-f]{8}/)).toBeVisible();
    await expect(row.getByText(/· v1/)).toBeVisible();
    await expect(row.getByText("Fixture output contains Test")).toBeVisible();
    await expect(row.getByText(/read-only/)).toBeVisible();
  } finally {
    // Leave no data behind: the suite shares one disposable database and later
    // specs (prompts/projects) assert exact prompt counts.
    const cleanup = await request.delete(`${DEV_API}/api/prompts/${prompt.id}`);
    expect(
      cleanup.ok(),
      "owner delete of the evaluation journey prompt",
    ).toBeTruthy();
  }
});
