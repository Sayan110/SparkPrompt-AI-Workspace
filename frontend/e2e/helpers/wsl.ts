/**
 * Phase 4F — minimal Windows ⇄ WSL helpers for the disposable E2E stack.
 *
 * Python (alembic, uvicorn, the ORM seed script) only exists inside WSL
 * (backend/.venv-linux), while Playwright runs on Windows. These helpers
 * derive the WSL path of this repository and run commands in the same
 * `wsl -d Ubuntu -- bash -c` shape the repo's existing scripts use.
 */
import { spawnSync } from "node:child_process";
import path from "node:path";

/** Absolute Windows path of the repository root (frontend/e2e/helpers → up 3). */
export const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
export const BACKEND_DIR = path.join(REPO_ROOT, "backend");

/** Convert "C:\a\b" → "/mnt/c/a/b" (the mount convention of this machine's WSL). */
export function wslPath(winPath: string): string {
  const match = /^([A-Za-z]):[\\/](.*)$/.exec(winPath);
  if (!match) {
    throw new Error(`Not a Windows path: ${winPath}`);
  }
  const drive = match[1].toLowerCase();
  const rest = match[2].replace(/\\/g, "/");
  return `/mnt/${drive}/${rest}`;
}

export type WslResult = { ok: boolean; stdout: string; stderr: string; code: number };

/** Run one bash command inside the Ubuntu WSL distro; never throws on failure. */
export function wslBash(command: string, timeoutMs = 120_000): WslResult {
  const result = spawnSync("wsl", ["-d", "Ubuntu", "--", "bash", "-c", command], {
    encoding: "utf8",
    timeout: timeoutMs,
    windowsHide: true,
  });
  return {
    ok: result.status === 0,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    code: result.status ?? -1,
  };
}

/** Run a command and fail loudly with captured output when it exits non-zero. */
export function wslBashOrThrow(command: string, what: string, timeoutMs = 120_000): string {
  const result = wslBash(command, timeoutMs);
  if (!result.ok) {
    throw new Error(
      `${what} failed (exit ${result.code}).\n--- stdout ---\n${result.stdout}\n--- stderr ---\n${result.stderr}`,
    );
  }
  return result.stdout;
}
