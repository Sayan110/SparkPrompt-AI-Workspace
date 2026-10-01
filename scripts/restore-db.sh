#!/usr/bin/env bash
# SparkPrompt — restore a backup into a DISPOSABLE database (Phase 4D, Step 18).
#
# Safety rules (enforced, not advisory):
#   * RESTORE_TARGET_DB is mandatory and may never be the live database name,
#     "postgres", or "template1" — this script exists for VERIFICATION only.
#   * It never drops or truncates anything: if the target already exists it
#     refuses and tells you to remove that disposable database yourself.
#   * Never point it at the development database; verification uses a fresh
#     disposable database that you drop afterwards (WITH FORCE).
#
# Required environment:
#   RESTORE_TARGET_DB  PGHOST  PGUSER
# Optional environment:
#   PGPORT (default 5432)  BACKUP_FILE (required, path to a .dump/.dump.gpg —
#   decrypt .gpg files before restoring)  LIVE_DATABASE (name to refuse,
#   default sparkprompt)
#   PG_RESTORE_BIN / CREATEDB_BIN / PSQL_BIN — same docker-exec override
#   pattern as backup-db.sh (values may CONTAIN ARGUMENTS and are
#   intentionally word-split).
#
# Exit codes: 0 restored, 1 usage/safety violation, 2 restore failure.
set -eu

PGPORT="${PGPORT:-5432}"
LIVE_DATABASE="${LIVE_DATABASE:-sparkprompt}"
PG_RESTORE_BIN="${PG_RESTORE_BIN:-pg_restore}"
CREATEDB_BIN="${CREATEDB_BIN:-createdb}"
PSQL_BIN="${PSQL_BIN:-psql}"

: "${RESTORE_TARGET_DB:?RESTORE_TARGET_DB is required (a disposable database name)}"
: "${PGHOST:?PGHOST is required}"
: "${PGUSER:?PGUSER is required}"
: "${BACKUP_FILE:?BACKUP_FILE is required (path to the dump to restore)}"

case "$RESTORE_TARGET_DB" in
  "$LIVE_DATABASE"|"postgres"|"template1")
    echo "restore: REFUSED — '$RESTORE_TARGET_DB' is not a disposable database." >&2
    echo "restore: verification must target a fresh disposable database." >&2
    exit 1
    ;;
  *[!A-Za-z0-9_]*)
    echo "restore: REFUSED — RESTORE_TARGET_DB may only contain letters, digits and underscores." >&2
    exit 1
    ;;
esac

if [ ! -f "$BACKUP_FILE" ]; then
  echo "restore: BACKUP_FILE not found: $BACKUP_FILE" >&2
  exit 1
fi

# Fail if the target already exists (never drop/overwrite in this script).
# shellcheck disable=SC2086  # command prefix may contain arguments
exists="$($PSQL_BIN -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname = '${RESTORE_TARGET_DB}'")"
if [ "$exists" = "1" ]; then
  echo "restore: REFUSED — database '$RESTORE_TARGET_DB' already exists." >&2
  echo "restore: drop that disposable database first, then re-run." >&2
  exit 1
fi

# shellcheck disable=SC2086  # command prefix may contain arguments
if ! $CREATEDB_BIN -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$RESTORE_TARGET_DB"; then
  echo "restore: could not create '$RESTORE_TARGET_DB' (check connectivity/permissions)." >&2
  exit 1
fi
echo "restore: created empty disposable database '$RESTORE_TARGET_DB'"

echo "restore: loading $(basename "$BACKUP_FILE") into '$RESTORE_TARGET_DB'..."
# shellcheck disable=SC2086  # command prefix may contain arguments
if $PG_RESTORE_BIN -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$RESTORE_TARGET_DB" \
    --no-owner --no-privileges <"$BACKUP_FILE"; then
  echo "restore: OK — verify row counts, then drop the disposable database."
else
  status=$?
  echo "restore: FAILED (exit $status); the disposable database was NOT removed" >&2
  echo "restore: automatically — inspect it, drop it, and retry." >&2
  exit 2
fi
