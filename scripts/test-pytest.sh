#!/usr/bin/env bash
# SparkPrompt - safe one-command pytest runner (Phase 5B: S2 + S3;
# Phase 5B.2: H1 ownership protocol, M1 single-server gate, M2 signal safety).
#
# WHAT EACH MODE DOES
#   (default, full database mode)
#     Requires the PostgreSQL container 'sparkprompt-postgres' to be ALREADY
#     RUNNING (this script never starts/stops containers or Docker Compose).
#       1. trip-wire: EXPECTED_DB must be the literal 'sparkprompt_test' and
#          FORBIDDEN_DB the literal 'sparkprompt', and they must differ.
#       2. read-only fingerprint (7 SELECTs) of the persistent 'sparkprompt'
#          database BEFORE anything destructive; aborts if unavailable.
#       3. SERVER IDENTITY GATE (before any destructive SQL): PGHOST/PGPORT
#          must be the compose-published loopback target (127.0.0.1 or
#          localhost, port 5432); unexpected operator values are REFUSED with
#          an explanation and are never silently overridden. Container-local
#          and host-TCP connections must then present byte-identical server
#          identity evidence (version | postmaster start epoch | postgres
#          database OID); mismatch or query failure aborts before CREATE,
#          DROP, migration or test.
#       4. builds DATABASE_URL in-process only (never printed, never written
#          to a file, never added to .env) from the VERIFIED host/port, and
#          requires it to end with the literal '/sparkprompt_test'.
#       5. proves the exact name via SELECT on pg_database. An existing
#          'sparkprompt_test' is dropped ONLY when the ownership protocol
#          below shows an EXACT token match between the local marker file
#          and the server-side database comment; a missing, malformed or
#          mismatched token stops the run without dropping anything.
#       6. generates a unique run token, then CREATE DATABASE
#          sparkprompt_test (literal), binds ownership with COMMENT ON
#          DATABASE (read-back verified) and writes the marker atomically
#          (same-directory temp file + rename, mode 0600). If ownership
#          cannot be established after the CREATE, automatic deletion is
#          REFUSED and manual inspection is required.
#       7. runs Alembic to head and the full pytest suite against it.
#       8. re-captures the fingerprint and compares (exit 5 on any change).
#       9. S3 gate: pytest exit 0 AND a parseable summary AND exactly
#          745 passed / 0 failed / 0 skipped, 0 reruns (also 0 errors/
#          xfailed/xpassed/deselected). A missing/unparseable summary can
#          never pass. Warnings are reported, never gated.
#      10. exit trap drops ONLY 'sparkprompt_test' (literal) and only after
#          a live re-check that its database comment equals this run's
#          token; a failed drop is reported loudly and downgrades a success
#          exit to 1.
#   --dbless
#     Runs pytest against an UNREACHABLE loopback target
#     (.../sparkprompt_test_dbless on port 9): no database connection can
#     succeed, so no database is used. The summary is parsed and printed but
#     this mode NEVER claims full verification (without PostgreSQL the suite
#     is documented to be partial). Diagnostic only. Diagnostic success exits
#     10 (L3, Phase 5B.8) so no caller can mistake it for a full integration
#     pass - --dbless never exits 0.
#   --help  This text.
#
# OWNERSHIP PROTOCOL (Phase 5B.2 / audit H1)
#   Every created disposable carries a server-side comment holding a unique
#   32-character lowercase hex run token (from /dev/urandom, fresh per run).
#   The same token is line 1 of $HOME/.sparkprompt_test_db.marker, written
#   via a same-directory temp file + atomic rename with mode 0600. A
#   pre-existing 'sparkprompt_test' is dropped only when the marker token
#   equals the LIVE server-side comment and both are well-formed tokens;
#   the marker's existence alone is never proof. Tokens are hex-only, so SQL
#   string quoting is unambiguous, and a token never becomes an identifier -
#   the database name stays the literal 'sparkprompt_test'. Tokens,
#   credentials and URLs are never printed.
#   Honest limits: this is possession-based, single-operator provenance. It
#   is NOT cryptographic proof and NOT multi-operator isolation. Residual
#   race: comment verification and DROP are separate statements (a swap in
#   between is not excluded); marker integrity assumes a trusted local
#   user/filesystem; restrictive permissions are best-effort on filesystems
#   without POSIX modes; SIGKILL cannot be trapped - an untrappable kill
#   between CREATE and ownership binding leaves a database that later runs
#   will refuse to delete (manual inspection required, by design).
#
# SERVER IDENTITY GATE (Phase 5B.2 / audit M1)
#   Fingerprint and all DDL run container-local (docker exec); migrations
#   and pytest run over host TCP using PGHOST:PGPORT. Before any destructive
#   SQL both paths run the SAME identity query (server version | postmaster
#   start epoch | postgres database OID) and must return byte-identical
#   evidence, proving they address the SAME PostgreSQL cluster. Unexpected
#   PGHOST/PGPORT values are refused with an explanation (never silently
#   overridden); if identity cannot be established the run stops instead of
#   weakening the check. Credentials and URLs are never printed.
#
# SIGNAL HANDLING (Phase 5B.2 / audit M2)
#   SIGINT and SIGTERM are trapped: the handler preserves the original
#   status (130 = INT, 143 = TERM), disables further signal traps and exits
#   through the SAME single EXIT cleanup path. Cleanup ignores INT/TERM
#   while it runs, executes at most once, drops only the literal
#   'sparkprompt_test', and only when the ownership protocol succeeds; a
#   signal can never turn a failed run into a success. SIGKILL is not
#   trappable (see ownership limits above).
#
# OUTPUT
#   "step:" progress lines, "gate:" safety/verification lines, pytest's own
#   output tee'd to a temp log (path printed), and one final "RESULT:" line.
#   DATABASE_URL, passwords and secret values are NEVER printed. The temp log
#   is removed after a successful run (exit 0 or 10) and retained - with its
#   path reported by the cleanup - on any failure (L11, Phase 5B.8).
#
# EXIT CODES
#   0   successful full-mode verification (pytest 0 + parseable summary +
#       745/0/0 + rerun=0 baseline + unchanged persistent fingerprint), or
#       successful display of --help/-h.
#   10  dbless mode diagnostic success: pytest 0 + parseable summary. NOT full
#       verification - --dbless never exits 0.
#   1   safety abort (trip-wire, ownership token missing/malformed/
#       mismatched, server identity unverifiable/mismatched, unexpected
#       PGHOST/PGPORT, fingerprint unavailable, target proof failed) or
#       cleanup could not confirm its DROP / refused to drop an unowned
#       database.
#   2   pytest exited non-zero (its status is printed first).
#   3   summary missing/unparseable, counts differ from 745/0/0, or any rerun
#       occurred.
#   4   environment prerequisite missing (docker/venv/container not running);
#       nothing is installed, started or stopped automatically.
#   5   persistent sparkprompt fingerprint CHANGED during the run (critical).
#   7   Alembic migration failed.
#   64  usage error.
#   130 interrupted by SIGINT  (guarded cleanup attempted; status preserved)
#   143 interrupted by SIGTERM (guarded cleanup attempted; status preserved)
#
# INVOCATION: bash scripts/test-pytest.sh [--dbless]
set -euo pipefail

EXPECTED_DB="sparkprompt_test"
FORBIDDEN_DB="sparkprompt"
PG_CONTAINER="sparkprompt-postgres"
BASELINE_PASSED=745
BASELINE_FAILED=0
BASELINE_SKIPPED=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
PY_BIN="$BACKEND_DIR/.venv-linux/bin/python"
ALEMBIC_BIN="$BACKEND_DIR/.venv-linux/bin/alembic"
PARSER="$SCRIPT_DIR/pytest_summary.py"
MARKER_FILE="${HOME}/.sparkprompt_test_db.marker"
# M1: identity evidence queried with the SAME SQL on both connection paths.
IDENTITY_SQL="SELECT current_setting('server_version') || '|' || EXTRACT(EPOCH FROM pg_postmaster_start_time())::text || '|' || (SELECT oid FROM pg_database WHERE datname='postgres')::text"
# H1: ownership state of THIS run, read by the exit cleanup (never printed).
TOKEN=""
OWNERSHIP=0
# L11: temporary pytest log path; the single EXIT cleanup removes it after a
# successful run (exit 0 or 10) and retains it - with its path - on failure.
LOG_FILE=""

usage() {
  # Print the leading '#' documentation block (everything before set -euo).
  awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
}

msg() { printf '%s\n' "$*"; }
step() { printf 'step: %s\n' "$*"; }
gate() { printf 'gate: %s\n' "$*"; }
die_safety() { printf 'SAFETY ABORT: %s\n' "$*" >&2; exit 1; }
die_env() { printf 'ENVIRONMENT: %s\n' "$*" >&2; exit 4; }

tripwire() {
  if [ "$EXPECTED_DB" != "sparkprompt_test" ]; then
    printf 'FATAL trip-wire: EXPECTED_DB must be the literal sparkprompt_test\n' >&2
    exit 1
  fi
  if [ "$FORBIDDEN_DB" != "sparkprompt" ]; then
    printf 'FATAL trip-wire: FORBIDDEN_DB must be the literal sparkprompt\n' >&2
    exit 1
  fi
  if [ "$FORBIDDEN_DB" = "$EXPECTED_DB" ]; then
    printf 'FATAL trip-wire: forbidden and expected database names collide\n' >&2
    exit 1
  fi
}

OWNS_DB=0
CLEANED=0

psql_admin() {
  # $1: a FIXED SQL statement written as a literal in this script only.
  docker exec "$PG_CONTAINER" psql -w -v ON_ERROR_STOP=1 -h 127.0.0.1 \
    -U sparkprompt -d postgres -tAc "$1"
}

capture_fingerprint() {
  # Read-only SELECTs against the persistent development database.
  docker exec "$PG_CONTAINER" psql -w -v ON_ERROR_STOP=1 -h 127.0.0.1 \
    -U sparkprompt -d sparkprompt -tAc \
    "SELECT 'alembic=' || COALESCE((SELECT version_num FROM alembic_version LIMIT 1), 'none'); SELECT 'users=' || count(*) FROM users; SELECT 'projects=' || count(*) FROM projects; SELECT 'prompts=' || count(*) FROM prompts; SELECT 'prompt_versions=' || count(*) FROM prompt_versions; SELECT 'prompt_runs=' || count(*) FROM prompt_runs; SELECT 'evaluation_records=' || count(*) FROM evaluation_records;"
}

drop_disposable() {
  # Literal-only DROP: refuses to run if either literal was ever altered, and
  # never interpolates a variable into the statement.
  if [ "$EXPECTED_DB" != "sparkprompt_test" ]; then return 1; fi
  docker exec "$PG_CONTAINER" psql -w -v ON_ERROR_STOP=1 -h 127.0.0.1 \
    -U sparkprompt -d postgres -tAc \
    "DROP DATABASE IF EXISTS sparkprompt_test WITH (FORCE);"
}

is_token() { # well-formed ownership token: exactly 32 lowercase hex chars
  [[ "${1:-}" =~ ^[0-9a-f]{32}$ ]]
}

db_comment() {
  # Live server-side ownership comment of the literal disposable database
  # (empty output when the comment is absent or the database does not exist).
  docker exec "$PG_CONTAINER" psql -w -v ON_ERROR_STOP=1 -h 127.0.0.1 \
    -U sparkprompt -d postgres -tAc \
    "SELECT COALESCE(shobj_description(oid, 'pg_database'), '') FROM pg_database WHERE datname = 'sparkprompt_test'"
}

set_db_comment() { # $1 = run token; MUST be hex-only so quoting is unambiguous
  # psql -c forbids psql-specific features (no :'var' interpolation there),
  # so the literal is inlined - safe because is_token() guarantees [0-9a-f]{32}
  # (no quote, backslash, dollar or semicolon can ever appear).
  is_token "$1" || return 1
  docker exec "$PG_CONTAINER" psql -w -v ON_ERROR_STOP=1 -h 127.0.0.1 \
    -U sparkprompt -d postgres -tAc \
    "COMMENT ON DATABASE sparkprompt_test IS '$1'"
}

write_marker() { # $1 = token; atomic same-directory temp file + rename, 0600
  local tmp="${MARKER_FILE}.tmp.$$"
  if ! (umask 077 && printf '%s\n' "$1" >"$tmp"); then
    rm -f "$tmp"
    return 1
  fi
  if ! chmod 600 "$tmp" 2>/dev/null; then
    rm -f "$tmp"
    return 1
  fi
  if ! mv -f "$tmp" "$MARKER_FILE"; then
    rm -f "$tmp"
    return 1
  fi
}

run_host_identity() { # host-TCP identity evidence; prints the evidence ONLY
  IDENTITY_DATABASE_URL="postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/postgres" \
    "$PY_BIN" - "$IDENTITY_SQL" <<'PY'
import os
import sys

url = os.environ.get("IDENTITY_DATABASE_URL", "")
sql = sys.argv[1] if len(sys.argv) > 1 else ""
if not url or not sql:
    sys.exit(3)
try:
    import psycopg

    with psycopg.connect(url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
except Exception:
    sys.exit(4)
if not row or row[0] is None or str(row[0]) == "":
    sys.exit(5)
sys.stdout.write(str(row[0]))
PY
}

verify_single_server() { # M1: one proven server BEFORE any destructive SQL
  case "${PGHOST:-}" in
    "") PGHOST="127.0.0.1" ;;
    127.0.0.1|localhost) ;;
    *)
      die_safety "PGHOST='${PGHOST}' is not permitted: this runner only accepts the compose-published loopback target 127.0.0.1 (or localhost). Your configuration is NOT overridden - unset PGHOST (or set it to 127.0.0.1) and re-run."
      ;;
  esac
  case "${PGPORT:-}" in
    "") PGPORT="5432" ;;
    5432) ;;
    *)
      die_safety "PGPORT='${PGPORT}' is not permitted: this runner only accepts the compose-published port 5432. Your configuration is NOT overridden - unset PGPORT (or set it to 5432) and re-run."
      ;;
  esac
  local cid hid
  cid="$(psql_admin "$IDENTITY_SQL")" ||
    die_safety "container-local server identity query failed - stopping before any destructive action"
  if [ -z "$cid" ]; then
    die_safety "container-local server identity is empty - stopping before any destructive action"
  fi
  if ! hid="$(run_host_identity)"; then
    die_safety "host-TCP server identity could not be established (connection or identity query failed) - stopping before any destructive action; this check is never weakened"
  fi
  if [ -z "$hid" ] || [ "$cid" != "$hid" ]; then
    die_safety "server identity MISMATCH: container-local [${cid}] vs host-TCP [${hid}] - refusing CREATE/DROP/migration/test; container-local DDL and host-TCP tests must address the same PostgreSQL server"
  fi
  gate "single-server gate: container-local and host-TCP identity evidence identical (version | postmaster epoch | postgres OID)"
  msg "gate: verified target ${PGHOST}:${PGPORT} through container '${PG_CONTAINER}'"
}

on_exit() {
  local rc=$?
  trap '' INT TERM # cleanup runs exactly once and cannot be re-entered
  set +e
  if [ "$OWNS_DB" = 1 ] && [ "$CLEANED" -eq 0 ]; then
    if [ "$OWNERSHIP" != 1 ]; then
      printf 'cleanup: REFUSED - this run created "sparkprompt_test" but ownership was never established.\n' >&2
      printf '  Automatic deletion of an unowned database is disabled. Inspect it manually:\n' >&2
      printf '  DROP DATABASE IF EXISTS sparkprompt_test WITH (FORCE);\n' >&2
      [ "$rc" -eq 0 ] && rc=1
    else
      local live_token=""
      if ! live_token="$(db_comment)"; then
        live_token=""
      fi
      if [ -z "$live_token" ] || [ "$live_token" != "${TOKEN:-}" ]; then
        printf 'cleanup: REFUSED - the server-side ownership comment is missing or does not match the token generated by this run.\n' >&2
        printf '  Disposable MAY REMAIN. Inspect it manually:\n' >&2
        printf '  DROP DATABASE IF EXISTS sparkprompt_test WITH (FORCE);\n' >&2
        [ "$rc" -eq 0 ] && rc=1
      elif drop_disposable; then
        CLEANED=1
        rm -f "$MARKER_FILE"
        msg "cleanup: dropped disposable database 'sparkprompt_test' (ownership re-verified against this run's token; marker removed)"
      else
        printf 'cleanup: FAILED - disposable database "sparkprompt_test" MAY REMAIN.\n' >&2
        printf '  If you are certain it is disposable, remove it manually:\n' >&2
        printf '  DROP DATABASE IF EXISTS sparkprompt_test WITH (FORCE);\n' >&2
        [ "$rc" -eq 0 ] && rc=1
      fi
    fi
  fi
  # L11: single-run temp log. Success (0 or dbless 10) -> remove; failure ->
  # retain with its path. rm runs under set +e and never reassigns $rc, so a
  # cleanup problem cannot mask the original exit status. This block executes
  # exactly once (the EXIT trap is guarded above), never deleting anything
  # other than this run's own mktemp file.
  if [ -n "$LOG_FILE" ]; then
    if [ "$rc" -eq 0 ] || [ "$rc" -eq 10 ]; then
      if rm -f "$LOG_FILE" 2>/dev/null; then
        msg "cleanup: removed temporary pytest log (run succeeded)"
      else
        printf 'cleanup: WARNING - could not remove temporary log %s (original exit status preserved)\n' "$LOG_FILE" >&2
      fi
    else
      msg "cleanup: retained temporary pytest log for diagnostics: $LOG_FILE"
    fi
  fi
  msg "cleanup: finished (script exit $rc)"
  exit "$rc"
}

on_signal() { # $1 = signal name, $2 = status to preserve (128 + signo)
  trap '' INT TERM # never re-enter; the EXIT trap performs cleanup exactly once
  printf 'SIGNAL: %s received - exiting %s through the guarded cleanup path\n' "$1" "$2" >&2
  exit "$2"
}

run_pytest() { # $1 = log file; sets PY_RC
  set +e
  (cd "$BACKEND_DIR" && "$PY_BIN" -m pytest 2>&1 | tee "$1")
  PY_RC=${PIPESTATUS[0]}
  set -e
  msg "gate: pytest exit status: $PY_RC"
}

summary_field() { # $1 = key; prints value, returns 1 when the key is missing
  local kv
  for kv in $SUMMARY_LINE; do
    if [ "${kv%%=*}" = "$1" ]; then
      printf '%s' "${kv#*=}"
      return 0
    fi
  done
  return 1
}

require_summary() { # $1 = log file; sets SUMMARY_LINE, exits 3 when missing
  if ! SUMMARY_LINE=$("$PY_BIN" "$PARSER" "$1"); then
    printf 'RESULT: FAIL (3) - pytest produced no parseable summary; a missing summary can never pass\n' >&2
    exit 3
  fi
  msg "gate: summary parsed: $SUMMARY_LINE"
}

count_gate() {
  local passed failed skipped errors xfailed xpassed deselected rerun
  passed="$(summary_field passed || :)"
  failed="$(summary_field failed || :)"
  skipped="$(summary_field skipped || :)"
  errors="$(summary_field errors || :)"
  xfailed="$(summary_field xfailed || :)"
  xpassed="$(summary_field xpassed || :)"
  deselected="$(summary_field deselected || :)"
  rerun="$(summary_field rerun || :)"
  if [ -z "$passed" ] || [ -z "$failed" ] || [ -z "$skipped" ]; then
    printf 'RESULT: FAIL (3) - summary lacks core counts: %s\n' "$SUMMARY_LINE" >&2
    exit 3
  fi
  if [ "$passed" -ne "$BASELINE_PASSED" ] || [ "$failed" -ne "$BASELINE_FAILED" ] ||
    [ "$skipped" -ne "$BASELINE_SKIPPED" ] || [ "${errors:-0}" -ne 0 ] ||
    [ "${xfailed:-0}" -ne 0 ] || [ "${xpassed:-0}" -ne 0 ] ||
    [ "${deselected:-0}" -ne 0 ] || [ "${rerun:-0}" -ne 0 ]; then
    printf 'RESULT: FAIL (3) - expected %s passed / %s failed / %s skipped / 0 reruns; got passed=%s failed=%s skipped=%s errors=%s xfailed=%s xpassed=%s deselected=%s rerun=%s\n' \
      "$BASELINE_PASSED" "$BASELINE_FAILED" "$BASELINE_SKIPPED" "$passed" "$failed" \
      "$skipped" "${errors:-0}" "${xfailed:-0}" "${xpassed:-0}" "${deselected:-0}" \
      "${rerun:-0}" >&2
    exit 3
  fi
  gate "count gate: ${BASELINE_PASSED} passed / 0 failed / 0 skipped / 0 reruns (and no errors/xfailed/xpassed/deselected)"
}

run_dbless() {
  step "dbless mode: unreachable loopback target - no database connection can succeed"
  export DATABASE_URL="postgresql+psycopg://sparkprompt@127.0.0.1:9/${EXPECTED_DB}_dbless"
  case "$DATABASE_URL" in
    */sparkprompt_test_dbless) ;;
    *) die_safety "constructed dbless URL failed the literal-suffix check" ;;
  esac
  [ -x "$PY_BIN" ] || die_env "missing $PY_BIN (WSL venv) - nothing will be installed"
  [ -f "$PARSER" ] || die_env "missing $PARSER"
  LOG_FILE="$(mktemp)"
  step "pytest (dbless, diagnostic) - log: $LOG_FILE"
  run_pytest "$LOG_FILE"
  require_summary "$LOG_FILE"
  msg "gate: DB-LESS run - NOT full verification (no database; without PostgreSQL the suite is documented as partial)"
  if [ "$PY_RC" -ne 0 ]; then
    printf 'RESULT: FAIL (2) - pytest exit %s in dbless mode\n' "$PY_RC" >&2
    exit 2
  fi
  msg "RESULT: PASS (10) - dbless diagnostic success; NOT full verification (run without --dbless for the database gate)"
  exit 10
}

run_full() {
  step "full database mode - target: literal disposable '${EXPECTED_DB}'"

  command -v docker >/dev/null 2>&1 ||
    die_env "docker client not found (this script never starts Docker)"
  docker ps --format '{{.Names}}' | grep -Fxq "$PG_CONTAINER" ||
    die_env "container '$PG_CONTAINER' is not running - start it yourself; this script never starts or stops containers"
  [ -x "$PY_BIN" ] || die_env "missing $PY_BIN (WSL venv) - nothing will be installed"
  [ -x "$ALEMBIC_BIN" ] || die_env "missing $ALEMBIC_BIN - nothing will be installed"
  [ -f "$PARSER" ] || die_env "missing $PARSER"

  # 1) read-only fingerprint BEFORE anything destructive
  FP_BEFORE="$(capture_fingerprint)" ||
    die_safety "cannot capture the persistent '${FORBIDDEN_DB}' fingerprint read-only - stopping before any destructive action"
  [ -n "$FP_BEFORE" ] ||
    die_safety "persistent fingerprint is empty - stopping before any destructive action"
  step "persistent '${FORBIDDEN_DB}' fingerprint captured (before)"

  # 2) server identity gate (M1), then process-scoped target - never printed,
  #    never written to any file
  PGUSER="${PGUSER:-sparkprompt}"
  PGPASSWORD="${PGPASSWORD:-sparkprompt}"
  verify_single_server
  export DATABASE_URL="postgresql+psycopg://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/${EXPECTED_DB}"
  case "$DATABASE_URL" in
    */sparkprompt_test) ;;
    *) die_safety "constructed DATABASE_URL does not end with the literal /sparkprompt_test - refusing" ;;
  esac

  # 3) existence + ownership (H1) BEFORE any DROP
  local exists marker_token db_token proven
  exists="$(psql_admin "SELECT 1 FROM pg_database WHERE datname = 'sparkprompt_test'")" ||
    die_safety "cannot read pg_database - stopping before destructive actions"
  if [ "$exists" = "1" ]; then
    db_token="$(db_comment)" ||
      die_safety "cannot read the server-side ownership comment - stopping without dropping anything"
    marker_token=""
    if [ -f "$MARKER_FILE" ]; then
      marker_token="$(head -n1 "$MARKER_FILE")" || marker_token=""
    fi
    if ! is_token "$marker_token"; then
      die_safety "database 'sparkprompt_test' exists but the ownership marker ($MARKER_FILE) is missing or malformed (expected a 32-hex run token on line 1). The marker alone is never proof of ownership; nothing will be dropped. Inspect it manually, drop it only if you know it is disposable, then re-run."
    fi
    if ! is_token "$db_token"; then
      die_safety "database 'sparkprompt_test' exists but its server-side ownership comment is missing or malformed (expected a 32-hex run token). Automatic deletion is refused; inspect it manually, drop it only if you know it is disposable, then re-run."
    fi
    if [ "$db_token" != "$marker_token" ]; then
      die_safety "ownership token mismatch between the marker ($MARKER_FILE) and the server-side database comment. Refusing to drop 'sparkprompt_test'; manual inspection required (drop it manually only if you know it is disposable), then re-run."
    fi
    step "existing '${EXPECTED_DB}' ownership verified: marker token equals the server-side comment - dropping it for a clean run"
    drop_disposable >/dev/null ||
      die_safety "could not drop the previous disposable database"
  fi

  # 4) run token + create + ownership binding + independent proof of target
  step "creating literal disposable database '${EXPECTED_DB}'"
  if ! TOKEN="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"; then
    die_safety "could not read /dev/urandom for a run token - refusing to create anything"
  fi
  is_token "$TOKEN" ||
    die_safety "run token from /dev/urandom is not 32 lowercase hex chars - refusing to create anything"
  psql_admin "CREATE DATABASE sparkprompt_test;" >/dev/null ||
    die_safety "CREATE DATABASE sparkprompt_test failed"
  OWNS_DB=1
  if ! set_db_comment "$TOKEN" >/dev/null; then
    die_safety "created 'sparkprompt_test' but could not bind ownership (COMMENT ON DATABASE failed). Automatic deletion of an unowned database is refused; inspect it manually and drop it only if you know it is disposable."
  fi
  db_token="$(db_comment)" ||
    die_safety "created 'sparkprompt_test' but could not re-read the ownership comment. Automatic deletion is refused; manual inspection required."
  if [ "$db_token" != "$TOKEN" ]; then
    die_safety "created 'sparkprompt_test' but the ownership comment read-back does not match the token generated by this run. Automatic deletion is refused; manual inspection required."
  fi
  OWNERSHIP=1
  if ! write_marker "$TOKEN"; then
    die_safety "ownership bound on the server but the marker file could not be written atomically with mode 0600 ($MARKER_FILE) - the exit cleanup re-verifies the comment and drops this run's disposable, then the run aborts"
  fi
  proven="$(psql_admin "SELECT datname FROM pg_database WHERE datname = 'sparkprompt_test'")" ||
    die_safety "cannot re-read pg_database after create"
  [ "$proven" = "sparkprompt_test" ] ||
    die_safety "post-create proof failed: pg_database returned '${proven}'"
  gate "target proven before migrations: pg_database = 'sparkprompt_test', ownership comment bound to this run's token, URL literal suffix OK, trip-wire OK"

  # 5) migrations (target proven above)
  step "alembic upgrade head (target sparkprompt_test)"
  if ! (cd "$BACKEND_DIR" && "$ALEMBIC_BIN" -c alembic.ini upgrade head); then
    printf 'RESULT: FAIL (7) - alembic migrations failed; the exit trap cleans the disposable\n' >&2
    exit 7
  fi

  # 6) pytest (S3 gate applied below)
  LOG_FILE="$(mktemp)"
  step "pytest (full suite) - log: $LOG_FILE"
  run_pytest "$LOG_FILE"

  # 7) fingerprint AFTER - checked before any other gate result
  local FP_AFTER
  FP_AFTER="$(capture_fingerprint)" ||
    die_safety "cannot re-capture the persistent fingerprint after the run"
  if [ "$FP_BEFORE" != "$FP_AFTER" ]; then
    printf 'RESULT: FAIL (5) - persistent %s FINGERPRINT CHANGED during the run\n' "$FORBIDDEN_DB" >&2
    printf -- '--- before ---\n%s\n--- after ---\n%s\n' "$FP_BEFORE" "$FP_AFTER" >&2
    exit 5
  fi
  gate "persistent fingerprint unchanged (alembic head + 7 table counts)"

  if [ "$PY_RC" -ne 0 ]; then
    printf 'RESULT: FAIL (2) - pytest exit %s\n' "$PY_RC" >&2
    exit 2
  fi
  require_summary "$LOG_FILE"
  count_gate
  msg "RESULT: PASS (0) - full suite verified against disposable '${EXPECTED_DB}'"
}

# --- entry point -------------------------------------------------------------
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi
if [ "$#" -gt 1 ]; then
  usage >&2
  exit 64
fi
MODE="full"
case "${1:-}" in
  "") ;;
  --dbless) MODE="dbless" ;;
  *) usage >&2; exit 64 ;;
esac

tripwire
trap on_exit EXIT
trap 'on_signal INT 130' INT
trap 'on_signal TERM 143' TERM
if [ "$MODE" = "dbless" ]; then
  run_dbless
else
  run_full
fi
