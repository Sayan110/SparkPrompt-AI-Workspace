/**
 * Phase 4F — cross-user isolation: a second browser context (user B) against
 * user A's data. UI proof: A's prompt never appears in B's library or search.
 * API proof from B's own authenticated request context: read, edit, delete and
 * versions of A's prompt all return 404 (route-level ownership checks), and
 * A's prompt survives untouched. Also proves the mirror direction (A cannot
 * see B's prompt).
 */
import { expect, test } from "../helpers/gate";
import { DEV_API, DEV_WEB, STORAGE_A, STORAGE_B } from "../config";
import { createPromptViaApi, listPromptsViaApi } from "../helpers/api-state";
import { gotoRoute } from "../helpers/ui";

// User A's session must exist before any page or API call in this spec.
test.use({ storageState: STORAGE_A });

const TITLE_A = "E2E isolation prompt for user A";
const IDEA_A = "E2E isolation idea A — must stay invisible to user B.";
const TITLE_B = "E2E isolation prompt for user B";
const IDEA_B = "E2E isolation idea B — must stay invisible to user A.";

test("user B cannot see or touch user A's prompt (UI + API)", async ({
  page,
  browser,
  gate,
}) => {
  // User A's data, created from A's own request context.
  const requestA = page.context().request;
  const promptA = await createPromptViaApi(requestA, {
    title: TITLE_A,
    idea: IDEA_A,
    body: "A-only body text.",
  });

  // Second, fully independent browser context for user B.
  const contextB = await browser.newContext({
    baseURL: DEV_WEB,
    storageState: STORAGE_B,
  });
  gate.watchContext(contextB);
  try {
    const requestB = contextB.request;
    await createPromptViaApi(requestB, {
      title: TITLE_B,
      idea: IDEA_B,
      body: "B-only body text.",
    });

    const pageB = await contextB.newPage(); // auto-watched via gate.watchContext

    // B's library shows B's prompt and never A's.
    await gotoRoute(pageB, "/library");
    await expect(
      pageB.locator("article").filter({ hasText: TITLE_B }),
    ).toHaveCount(1);
    await expect(
      pageB.locator("article").filter({ hasText: TITLE_A }),
    ).toHaveCount(0);

    // Searching for A's exact title in B's library finds nothing.
    await pageB.getByLabel("Search saved prompts").fill(TITLE_A);
    await expect(pageB.getByText(/No prompts match/)).toBeVisible();

    // Direct API attempts by B against A's prompt: read, edit, delete,
    // versions — all 404 (ownership is enforced at the route layer).
    const readAttempt = await requestB.get(`${DEV_API}/api/prompts/${promptA.id}`);
    expect(readAttempt.status(), "B read A's prompt").toBe(404);
    const editAttempt = await requestB.put(`${DEV_API}/api/prompts/${promptA.id}`, {
      data: { title: "stolen title" },
    });
    expect(editAttempt.status(), "B edit A's prompt").toBe(404);
    const deleteAttempt = await requestB.delete(
      `${DEV_API}/api/prompts/${promptA.id}`,
    );
    expect(deleteAttempt.status(), "B delete A's prompt").toBe(404);
    const versionsAttempt = await requestB.get(
      `${DEV_API}/api/prompts/${promptA.id}/versions`,
    );
    expect(versionsAttempt.status(), "B list A's versions").toBe(404);

    // A's data is fully intact after all four attempts.
    const intact = await requestA.get(`${DEV_API}/api/prompts/${promptA.id}`);
    expect(intact.status()).toBe(200);
    expect(((await intact.json()) as { title: string }).title).toBe(TITLE_A);

    // Mirror direction: A's library shows A's prompt, not B's.
    await gotoRoute(page, "/library");
    await expect(
      page.locator("article").filter({ hasText: TITLE_A }),
    ).toHaveCount(1);
    await expect(
      page.locator("article").filter({ hasText: TITLE_B }),
    ).toHaveCount(0);

    // Each user's list contains exactly its own isolation prompt.
    const listA = await listPromptsViaApi(requestA);
    const listB = await listPromptsViaApi(requestB);
    expect(listA.map((item) => item.title)).toContain(TITLE_A);
    expect(listA.map((item) => item.title)).not.toContain(TITLE_B);
    expect(listB.map((item) => item.title)).toContain(TITLE_B);
    expect(listB.map((item) => item.title)).not.toContain(TITLE_A);

    // Leave no data behind: the suite shares one disposable database and later
    // specs (projects) assert exact prompt counts in the default project.
    // (On failure the run's database is recreated anyway — teardown + next run.)
    const promptB = listB.find((item) => item.title === TITLE_B);
    if (!promptB) throw new Error("B's isolation prompt missing from B's list before cleanup");
    const cleanupA = await requestA.delete(`${DEV_API}/api/prompts/${promptA.id}`);
    expect(cleanupA.ok(), "owner delete of A's isolation prompt").toBeTruthy();
    const cleanupB = await requestB.delete(`${DEV_API}/api/prompts/${promptB.id}`);
    expect(cleanupB.ok(), "owner delete of B's isolation prompt").toBeTruthy();
  } finally {
    await contextB.close();
  }
});
