/**
 * Phase 4F — Playwright configuration (the only browser testing framework).
 *
 * Default run (`npm run test:e2e` / `npm run test:e2e:ui`):
 *   1. boots the disposable stack — FastAPI on :8100 with the throwaway
 *      sparkprompt_e2e database, Next.js on :3000 built against :8100 —
 *   2. runs the `setup` project (deterministic accounts → storageState),
 *   3. runs the `app` project (all dev-mode journeys, serial: workers 1),
 *   4. tears the disposable stack down (global-teardown).
 *
 * Production run (`npm run test:e2e:prod`): E2E_SKIP_DEV_STACK=1 is set by
 * e2e/scripts/run-prod.mjs, so only the `production` project exists and no
 * dev stack is started — the suite targets the real proxy at :8080
 * (DEPLOYMENT.md §11a) which must already be up.
 */
import { defineConfig } from "@playwright/test";

const DEV_WEB = "http://localhost:3000";
const PROD_BASE = process.env.E2E_PROD_BASE_URL ?? "http://localhost:8080";

const devStack = !process.env.E2E_SKIP_DEV_STACK;

export default defineConfig({
  testDir: "./e2e",
  // Serial by design: shared disposable DB, process-local rate limiters,
  // deterministic data names (Step 29 of the 4F plan).
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],
  outputDir: "test-results",
  globalTeardown: devStack ? "./e2e/scripts/global-teardown.ts" : undefined,
  use: {
    baseURL: DEV_WEB,
    // Preinstalled Microsoft Edge — avoids Playwright's browser download path
    // entirely (documented 4F advisory mitigation for GHSA-7mvr-c777-76hp on
    // @playwright/test 1.51.1, which the approved plan pins).
    channel: "msedge",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
  projects: devStack
    ? [
        { name: "setup", testMatch: /auth[\\/]setup\.ts$/ },
        {
          name: "app",
          testDir: "./e2e",
          testIgnore: [/production[\\/]/, /auth[\\/]setup\.ts$/],
          dependencies: ["setup"],
        },
      ]
    : [
        { name: "prod-setup", testDir: "./e2e/production", testMatch: /prod\.setup\.ts$/ },
        {
          name: "production",
          testDir: "./e2e/production",
          testIgnore: /prod\.setup\.ts$/,
          dependencies: ["prod-setup"],
          use: { baseURL: PROD_BASE },
        },
      ],
  webServer: devStack
    ? [
        {
          command: "node e2e/scripts/start-api.mjs",
          url: "http://127.0.0.1:8100/api/health",
          timeout: 120_000,
          reuseExistingServer: false,
          stdout: "pipe",
          stderr: "pipe",
        },
        {
          command: "node e2e/scripts/start-web.mjs",
          url: "http://127.0.0.1:3000",
          timeout: 300_000,
          reuseExistingServer: false,
          stdout: "pipe",
          stderr: "pipe",
        },
      ]
    : undefined,
});
