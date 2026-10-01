/**
 * Phase 4F — disposable stack teardown (runs after the dev-mode suite).
 *
 * Order matters: kill the E2E uvicorn FIRST (its lifespan owns the database
 * connection), then drop the disposable database. The Next.js server is
 * stopped by Playwright itself (it owns that webServer child).
 *
 * Everything here is hard-scoped to sparkprompt_e2e / port 8100.
 */
import { spawnSync } from "node:child_process";

const EXPECTED_DB: string = "sparkprompt_e2e";
const FORBIDDEN_DB: string = "sparkprompt";

function wslBash(command: string): void {
  spawnSync("wsl", ["-d", "Ubuntu", "--", "bash", "-c", command], {
    encoding: "utf8",
    timeout: 60_000,
    windowsHide: true,
  });
}

export default async function globalTeardown(): Promise<void> {
  if (EXPECTED_DB !== "sparkprompt_e2e" || EXPECTED_DB === FORBIDDEN_DB) {
    throw new Error("guard tripped: disposable database name is wrong — refusing to run");
  }

  // 1. Stop the disposable API (pattern bracketed so bash cannot self-match).
  wslBash("pkill -f 'po[r]t 8100' || true");
  process.stdout.write("[e2e-teardown] uvicorn :8100 stopped\n");

  // 2. Drop ONLY the disposable database (WITH (FORCE) clears live sockets).
  //    docker is invoked INSIDE WSL as root — identical to this machine's own
  //    docker.bat wrapper. No cmd.exe layer, no stdin piping (both proven broken
  //    here: cmd eats piped stdin; Node does not quote shell:true args —
  //    DEP0190). The SQL is single-quoted for bash and passed by Node as one
  //    verbatim argv element (no Windows shell involved). `-w` never prompts.
  const dropSql = `DROP DATABASE IF EXISTS ${EXPECTED_DB} WITH (FORCE);`;
  if (dropSql.includes("'")) {
    throw new Error("refusing SQL that contains a single quote (would break bash quoting)");
  }
  const result = spawnSync(
    "wsl",
    [
      "-d",
      "Ubuntu",
      "-u",
      "root",
      "--",
      "bash",
      "-c",
      "docker exec -e PGPASSWORD=sparkprompt sparkprompt-postgres " +
        "psql -w -v ON_ERROR_STOP=1 -h 127.0.0.1 -U sparkprompt -d postgres " +
        `-c '${dropSql}'`,
    ],
    { encoding: "utf8", windowsHide: true, timeout: 60_000 },
  );
  if (result.status !== 0) {
    throw new Error(
      `dropping ${EXPECTED_DB} failed: ${result.stderr || result.stdout} (the persistent ${FORBIDDEN_DB} was never targeted)`,
    );
  }
  process.stdout.write(`[e2e-teardown] ${EXPECTED_DB} dropped (persistent stack untouched)\n`);
}
