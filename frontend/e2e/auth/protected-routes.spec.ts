/**
 * Phase 4F — protected-route gating for anonymous visitors across every
 * top-level route (4A/4B semantics: AppShell boots, finds no session, and
 * replaces the route with /login before any workspace content renders).
 */
import { expect, test } from "../helpers/gate";
import { ROUTE_TITLES, type RoutePath } from "../helpers/ui";

const PROTECTED_ROUTES: RoutePath[] = [
  "/dashboard",
  "/studio",
  "/library",
  "/projects",
  "/lab",
  "/settings",
];

test("every top-level route redirects anonymous visitors to /login", async ({
  page,
  gate,
}) => {
  // Observed 4F behavior (frozen 4D client design, declared honestly): the
  // protected layout mounts its data hooks (prompts, projects, AI providers)
  // in the same commit that starts the client-side redirect, so those fetches
  // leave before the gate replaces the route and the API correctly answers
  // 401 for the anonymous session. Test-scoped declaration keeps the global
  // allowlist strict.
  gate.expectStatus(
    401,
    /\/api\/(?:prompts|projects|ai\/providers)(?:[/?]|$)/,
    "anonymous data hooks fire in the same commit as the client-side redirect to /login (frozen 4D layout behavior)",
  );

  for (const route of PROTECTED_ROUTES) {
    await page.goto(route);
    await page.waitForURL("**/login");
    // Workspace content never rendered: no sidebar, no topbar heading.
    await expect(
      page.getByRole("navigation", { name: "Main navigation" }),
    ).toHaveCount(0);
    for (const title of Object.values(ROUTE_TITLES)) {
      await expect(page.getByRole("heading", { level: 1, name: title })).toHaveCount(
        0,
      );
    }
    // Fresh anonymous state for the next route (provider user state is gone).
    await expect(
      page.getByRole("heading", { level: 1, name: "Welcome back" }),
    ).toBeVisible();
  }
});
