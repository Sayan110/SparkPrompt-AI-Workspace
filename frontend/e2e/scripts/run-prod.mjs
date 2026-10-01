#!/usr/bin/env node
/**
 * Phase 4F — run only the production-proxy browser suite.
 *
 * Sets E2E_SKIP_DEV_STACK=1 so playwright.config.ts exposes ONLY the
 * `production` project and starts no dev stack. The real proxy under test is
 * the DEPLOYMENT.md §11a stack at http://localhost:8080, which must already
 * be up (this script neither starts nor stops it).
 */
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

const result = spawnSync("npx", ["playwright", "test"], {
  cwd: frontendDir,
  stdio: "inherit",
  env: { ...process.env, E2E_SKIP_DEV_STACK: "1" },
  shell: process.platform === "win32",
  windowsHide: true,
});

process.exit(result.status ?? 1);
