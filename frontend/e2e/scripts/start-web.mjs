#!/usr/bin/env node
/**
 * Phase 4F — disposable E2E web stack boot (Next.js on :3000).
 *
 * Builds the frontend against the disposable API (:8100) — NEXT_PUBLIC_API_URL
 * is baked at build time — then runs `next start`. The script stays alive as a
 * babysitter for Playwright's webServer wiring.
 *
 * Note (documented in e2e/README.md): the resulting .next build is E2E-
 * configured; the final 4F verification gate re-runs a clean `npm run build`.
 */
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const WEB_PORT = 3000;
const API_URL = "http://localhost:8100";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(here, "..", "..");
const nextBin = path.join(frontendDir, "node_modules", "next", "dist", "bin", "next");

function log(message) {
  process.stdout.write(`[e2e-web] ${message}\n`);
}

function fail(message) {
  process.stderr.write(`[e2e-web] ERROR: ${message}\n`);
  process.exit(1);
}

/* 1. Free :3000 if — and only if — the squatter is a leftover Node process.
      Any other owner means a user process: refuse instead of killing blindly. */
const netstat = spawnSync("netstat", ["-ano"], { encoding: "utf8", windowsHide: true });
const listeners = (netstat.stdout ?? "")
  .split("\n")
  .filter((line) => new RegExp(`:${WEB_PORT}\\s`).test(line) && line.includes("LISTENING"))
  .map((line) => line.trim().split(/\s+/).pop())
  .filter((pid) => /^\d+$/.test(pid ?? ""));

for (const pid of [...new Set(listeners)]) {
  const tasklist = spawnSync("tasklist", ["/FI", `PID eq ${pid}`, "/FO", "CSV", "/NH"], {
    encoding: "utf8",
    windowsHide: true,
  });
  const image = (tasklist.stdout ?? "").split(",")[0] ?? "";
  if (!image.toLowerCase().includes("node")) {
    fail(`port ${WEB_PORT} is owned by a non-Node process (${image.trim()}, pid ${pid}) — refusing to touch it.`);
  }
  log(`removing leftover Node listener on :${WEB_PORT} (pid ${pid})`);
  spawnSync("taskkill", ["/PID", pid, "/T", "/F"], { windowsHide: true });
}

/* 2. Build with the disposable API origin baked in. */
const build = spawnSync(process.execPath, [nextBin, "build"], {
  cwd: frontendDir,
  stdio: "inherit",
  env: { ...process.env, NEXT_PUBLIC_API_URL: API_URL },
  windowsHide: true,
});
if (build.status !== 0) fail(`next build exited with ${build.status}`);
log(`next build complete (NEXT_PUBLIC_API_URL=${API_URL})`);

/* 3. Start the server. */
const server = spawn(process.execPath, [nextBin, "start", "-p", String(WEB_PORT)], {
  cwd: frontendDir,
  stdio: "inherit",
  env: { ...process.env, NEXT_PUBLIC_API_URL: API_URL },
  windowsHide: true,
});
server.on("exit", (code, signal) => {
  fail(`next start exited unexpectedly (code=${code} signal=${signal})`);
});

/* 4. Wait for liveness, then babysit. */
for (let i = 1; i <= 90; i += 1) {
  try {
    const response = await fetch(`http://127.0.0.1:${WEB_PORT}/`);
    if (response.ok || response.status === 404) break;
  } catch {
    /* not up yet */
  }
  if (i === 90) fail(`next start did not answer on :${WEB_PORT} in 90s`);
  await new Promise((resolve) => setTimeout(resolve, 1000));
}
log(`next start ready on :${WEB_PORT}`);

let failures = 0;
const timer = setInterval(async () => {
  try {
    const response = await fetch(`http://127.0.0.1:${WEB_PORT}/`);
    failures = response.ok || response.status < 500 ? 0 : failures + 1;
  } catch {
    failures += 1;
  }
  if (failures >= 3) {
    clearInterval(timer);
    fail(`next start became unresponsive (${failures} consecutive failures)`);
  }
}, 5000);

function shutdown() {
  clearInterval(timer);
  server.kill();
  process.exit(0);
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
