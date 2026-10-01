#!/usr/bin/env node
/**
 * Phase 4F — disposable E2E API stack boot.
 *
 * Targets ONLY disposable resources (hard-guarded below):
 *   database : sparkprompt_e2e   (never the persistent `sparkprompt` database)
 *   port     : 8100              (dev API owns 8000, prod proxy owns 8080)
 *   process  : one uvicorn started here (killed by global-teardown)
 *
 * Sequence: kill stale :8100 uvicorn → drop+create sparkprompt_e2e →
 * alembic upgrade head → start uvicorn → wait for /api/health + ready.
 * The script stays alive as a babysitter so Playwright's webServer wiring
 * (readiness, logs, shutdown) behaves identically to a single command.
 */
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import os from "node:os";
import path from "node:path";

const EXPECTED_DB = "sparkprompt_e2e";
const FORBIDDEN_DB = "sparkprompt";
const PORT = 8100;
/* The uvicorn log lives on the Windows filesystem (durable), not in WSL's
   /tmp — observed on this machine: WSL /tmp files are wiped within seconds
   without a reboot, and detached background processes started by a wsl.exe
   session die at session exit. The log is readable from both sides
   (directly from Windows, or via /mnt/c… from WSL). */
const LOG_WIN = path.join(os.tmpdir(), "opencode", "sparkprompt-e2e-8100.log");
const LOG_WSL = toWsl(LOG_WIN);

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(here, "..", "..");
const repoRoot = path.resolve(frontendDir, "..");
const backendWsl = toWsl(path.join(repoRoot, "backend"));
const databaseUrl = `postgresql+psycopg://sparkprompt:sparkprompt@127.0.0.1:5432/${EXPECTED_DB}`;

function log(message) {
  process.stdout.write(`[e2e-api] ${message}\n`);
}

function fail(message) {
  process.stderr.write(`[e2e-api] ERROR: ${message}\n`);
  process.exit(1);
}

function toWsl(winPath) {
  const match = /^([A-Za-z]):[\\/](.*)$/.exec(winPath);
  if (!match) fail(`not a Windows path: ${winPath}`);
  return `/mnt/${match[1].toLowerCase()}/${match[2].replace(/\\/g, "/")}`;
}

function wslBash(command, what, timeoutMs = 120_000) {
  const result = spawnSync("wsl", ["-d", "Ubuntu", "--", "bash", "-c", command], {
    encoding: "utf8",
    timeout: timeoutMs,
    windowsHide: true,
  });
  if (result.status !== 0) {
    fail(
      `${what} failed (exit ${result.status})\n--- stdout ---\n${result.stdout ?? ""}\n--- stderr ---\n${result.stderr ?? ""}`,
    );
  }
  return result.stdout ?? "";
}

/** psql with retries: the postgres container may still be starting up after a cold boot.
 *  Runs docker INSIDE WSL as root — exactly how this machine's own docker.bat
 *  wrapper invokes it (root sidesteps both the group check and the daemon
 *  cold-start, which is also ensured above for safety). No cmd.exe layer and no
 *  stdin piping: the SQL is single-quoted for the bash -c string, which Node
 *  passes verbatim as one argv element (no shell involved on the Windows side).
 *  shell:true / docker.bat were both proven broken here: cmd eats piped stdin
 *  and Node does not quote shell:true arguments (DEP0190).
 *  Guard: our statements never contain single quotes (enforced below). */
function psql(sql, what) {
  if (sql.includes("'")) {
    fail("psql(): refusing SQL that contains a single quote (would break bash quoting)");
  }
  const command =
    "(systemctl is-active --quiet docker || systemctl start docker || service docker start || true) && " +
    "docker exec -e PGPASSWORD=sparkprompt sparkprompt-postgres " +
    "psql -w -v ON_ERROR_STOP=1 -h 127.0.0.1 -U sparkprompt -d postgres " +
    `-c '${sql}'`;
  const deadline = Date.now() + 60_000;
  let lastError = "";
  for (;;) {
    const result = spawnSync(
      "wsl",
      ["-d", "Ubuntu", "-u", "root", "--", "bash", "-c", command],
      { encoding: "utf8", windowsHide: true, timeout: 60_000 },
    );
    if (result.status === 0) return;
    lastError = `${result.stderr || result.stdout || result.error || "no output"}`;
    if (Date.now() >= deadline) {
      fail(`${what} failed after retries:\n${lastError}`);
    }
    spawnSync("ping", ["-n", "3", "127.0.0.1"], { windowsHide: true });
  }
}

/* ---- Hard guards: this script may only ever touch the disposable stack. ---- */
if (EXPECTED_DB !== "sparkprompt_e2e" || EXPECTED_DB === FORBIDDEN_DB) {
  fail("guard tripped: disposable database name is wrong — refusing to run.");
}

/* 1. Kill any stale uvicorn from a crashed previous run (pattern is bracketed
      so the bash process that carries it cannot match itself). */
wslBash("pkill -f 'po[r]t 8100' || true", "stopping stale :8100 uvicorn");

/* 2. Recreate the disposable database. DROP/CREATE run as two statements
      (CREATE DATABASE cannot execute inside a transaction block). */
psql(`DROP DATABASE IF EXISTS ${EXPECTED_DB} WITH (FORCE);`, `drop ${EXPECTED_DB}`);
psql(`CREATE DATABASE ${EXPECTED_DB};`, `create ${EXPECTED_DB}`);
log(`database ${EXPECTED_DB} recreated (persistent ${FORBIDDEN_DB} untouched)`);

/* 3. Migrate the disposable database to head (identical alembic path as dev/prod). */
wslBash(
  `cd '${backendWsl}' && DATABASE_URL='${databaseUrl}' .venv-linux/bin/alembic -c '${backendWsl}/alembic.ini' upgrade head`,
  "alembic upgrade head",
);
log("alembic upgrade head complete");

/* 4. Start uvicorn on :8100 as a FOREGROUND child held open by wsl.exe for
      uvicorn's whole lifetime. Empirically on this machine, a detached
      background start (`{ … setsid … } &`) dies the moment its wsl.exe
      session exits — before liveness can even be probed — so the wsl call
      must stay alive as long as the server does. `exec` replaces bash with
      python (wsl.exe exits exactly when uvicorn does); `-u` keeps the log
      unbuffered; the log file is on the durable Windows filesystem. */
let uvicornExited = false;
const uvicornChild = spawn(
  "wsl",
  [
    "-d",
    "Ubuntu",
    "--",
    "bash",
    "-c",
    `cd '${backendWsl}' && DATABASE_URL='${databaseUrl}' exec ` +
      `'${backendWsl}/.venv-linux/bin/python' -u -m uvicorn app.main:app ` +
      `--host 0.0.0.0 --port ${PORT} </dev/null >'${LOG_WSL}' 2>&1`,
  ],
  { stdio: "ignore", windowsHide: true },
);
uvicornChild.on("exit", (code, signal) => {
  uvicornExited = true;
  process.stderr.write(
    `[e2e-api] uvicorn child exited (code=${code} signal=${signal}) — log: ${LOG_WIN}\n`,
  );
});
uvicornChild.on("error", (error) => {
  fail(`could not spawn the uvicorn wsl child: ${String(error)}`);
});

/* 5. Wait for liveness and migration-verified readiness. */
async function waitFor(url, what, attempts) {
  for (let i = 1; i <= attempts; i += 1) {
    if (uvicornExited) break;
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  if (uvicornExited) {
    fail(`${what}: the uvicorn child exited before becoming ready — see ${LOG_WIN}`);
  }
  fail(`${what} did not answer in ${attempts}s — see ${LOG_WIN}.`);
}

await waitFor(`http://127.0.0.1:${PORT}/api/health`, "GET /api/health", 60);
await waitFor(`http://127.0.0.1:${PORT}/api/health/ready`, "GET /api/health/ready", 30);
log(`uvicorn ready on :8100 (log: ${LOG_WIN})`);

/* 6. Babysit until Playwright stops this command. */
let failures = 0;
const timer = setInterval(async () => {
  try {
    const response = await fetch(`http://127.0.0.1:${PORT}/api/health`);
    failures = response.ok ? 0 : failures + 1;
  } catch {
    failures += 1;
  }
  if (failures >= 3) {
    clearInterval(timer);
    fail(`uvicorn :8100 became unresponsive (${failures} consecutive failures)`);
  }
}, 5000);

function shutdown() {
  clearInterval(timer);
  spawnSync("wsl", ["-d", "Ubuntu", "--", "bash", "-c", `pkill -f 'po[r]t 8100' || true`], {
    windowsHide: true,
  });
  if (!uvicornExited) {
    uvicornChild.kill("SIGTERM");
  }
  process.exit(0);
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
