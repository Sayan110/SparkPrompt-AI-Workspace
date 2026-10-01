/**
 * Phase 4F — projects journey: server-truth counts, UI create, assignment
 * both directions, UI edit, UI delete. Assignment runs through PUT
 * /api/prompts/{id} because the product UI intentionally exposes no
 * assignment control (observed fact, documented in the 4F report); the
 * project cards' prompt counts are the UI's proof of the server state.
 */
import { expect, test } from "../helpers/gate";
import { STORAGE_A } from "../config";
import {
  createPromptViaApi,
  listProjectsViaApi,
  updatePromptViaApi,
} from "../helpers/api-state";
import { expectToast, gotoRoute } from "../helpers/ui";

test.use({ storageState: STORAGE_A });

const PROJECT_NAME = "E2E Project Alpha";
const RENAMED_NAME = "E2E Project Alpha Renamed";
const SEED_TITLE = "E2E projects counter prompt";
const SEED_IDEA = "E2E projects counter idea — proves prompt counting and assignment.";
const SEED_BODY = "Counter body for the projects journey.";

test("project lifecycle: counts, create, assign both ways, edit, delete", async ({
  page,
}) => {
  const request = page.context().request;

  // Seed one prompt for user A → the server auto-creates the default project.
  const prompt = await createPromptViaApi(request, {
    title: SEED_TITLE,
    idea: SEED_IDEA,
    body: SEED_BODY,
  });
  expect(prompt.project_id, "prompt lands in an owning project").toBeTruthy();

  await gotoRoute(page, "/projects");
  await expect(page.getByText("All projects")).toBeVisible();
  await expect(page.getByText("1 prompt")).toBeVisible();

  // Create through the UI.
  await page.getByRole("button", { name: "New project" }).click();
  const createDialog = page.getByRole("dialog");
  await page.getByLabel("Name").fill(PROJECT_NAME);
  await page
    .getByLabel(/Description/)
    .fill("Disposable Phase 4F project fixture.");
  await createDialog.getByRole("button", { name: "Create project" }).click();
  await expectToast(page, "Project created.");
  await expect(
    page.getByRole("heading", { level: 3, name: PROJECT_NAME, exact: true }),
  ).toBeVisible();
  await expect(page.getByText("2 total")).toBeVisible();

  const projects = await listProjectsViaApi(request);
  const alpha = projects.find((item) => item.name === PROJECT_NAME);
  const defaultProject = projects.find((item) => item.id !== alpha?.id);
  if (!alpha || !defaultProject) {
    throw new Error("expected exactly default + created project after seed");
  }

  // Card scoping: the card is the heading's direct parent container.
  const cardOf = (name: string) =>
    page.getByRole("heading", { level: 3, name, exact: true }).locator("xpath=..");

  // Assign seed → alpha (API), reload, and read the counts back from the UI.
  await updatePromptViaApi(request, prompt.id, { project_id: alpha.id });
  await gotoRoute(page, "/projects");
  await expect(cardOf(PROJECT_NAME).getByText("1 prompt")).toBeVisible();
  await expect(cardOf(defaultProject.name).getByText("0 prompts")).toBeVisible();

  // Assign back → default project; counts flip on both cards.
  await updatePromptViaApi(request, prompt.id, { project_id: defaultProject.id });
  await gotoRoute(page, "/projects");
  await expect(cardOf(defaultProject.name).getByText("1 prompt")).toBeVisible();
  await expect(cardOf(PROJECT_NAME).getByText("0 prompts")).toBeVisible();

  // Edit through the UI.
  await cardOf(PROJECT_NAME).getByRole("button", { name: "Edit", exact: true }).click();
  const editDialog = page.getByRole("dialog");
  await expect(editDialog.getByText("Edit project")).toBeVisible();
  await page.getByLabel("Name").fill(RENAMED_NAME);
  await editDialog.getByRole("button", { name: "Save changes" }).click();
  await expectToast(page, "Project updated.");
  await expect(
    page.getByRole("heading", { level: 3, name: RENAMED_NAME, exact: true }),
  ).toBeVisible();

  // Delete the (now empty) project through the UI.
  await page
    .getByRole("button", { name: `Delete ${RENAMED_NAME}` })
    .click();
  const deleteDialog = page.getByRole("dialog");
  await expect(deleteDialog.getByText("Delete project?")).toBeVisible();
  await deleteDialog.getByRole("button", { name: "Delete", exact: true }).click();
  await expectToast(page, "Project deleted.");
  await expect(
    page.getByRole("heading", { level: 3, name: RENAMED_NAME, exact: true }),
  ).toHaveCount(0);

  // Server truth after the delete: the project is gone, the default remains.
  const remaining = await listProjectsViaApi(request);
  expect(remaining.map((item) => item.name)).toContain(defaultProject.name);
  expect(remaining.map((item) => item.name)).not.toContain(RENAMED_NAME);
});
