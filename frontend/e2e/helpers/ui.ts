/**
 * Phase 4F — shared UI helpers (navigation, toasts, session controls).
 */
import { expect, type Locator, type Page } from "@playwright/test";

/** Topbar heading (h1) rendered by AppShell for each authenticated route. */
export const ROUTE_TITLES = {
  "/dashboard": "Dashboard",
  "/studio": "Magic Studio",
  "/library": "Library",
  "/projects": "Projects",
  "/lab": "Lab",
  "/settings": "Settings",
} as const;

export type RoutePath = keyof typeof ROUTE_TITLES;

/** Toast locator filtered by text (toasts use role="status"; AppShell's loader does too). */
export function toast(page: Page, text: string | RegExp): Locator {
  return page.getByRole("status").filter({ hasText: text });
}

export async function expectToast(page: Page, text: string | RegExp): Promise<void> {
  await expect(toast(page, text)).toBeVisible();
}

/** Navigate to an authenticated route and wait for the app shell to be interactive. */
export async function gotoRoute(page: Page, route: RoutePath): Promise<void> {
  await page.goto(route);
  await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
  // exact: true — the route's PageHeader h1 may CONTAIN the route name
  // (e.g. /projects → "Projects, without the clutter.") while the topbar h1
  // is the bare name; substring matching would hit both.
  await expect(
    page.getByRole("heading", { level: 1, name: ROUTE_TITLES[route], exact: true }),
  ).toBeVisible();
}

/** Wait until the workspace finished booting (no "Loading workspace…" placeholder). */
export async function waitForWorkspace(page: Page): Promise<void> {
  await expect(page.getByRole("status").filter({ hasText: "Loading workspace…" })).toHaveCount(0);
}
