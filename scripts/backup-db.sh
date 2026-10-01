#!/usr/bin/env bash
# SparkPrompt — PostgreSQL logical backup via pg_dump (Phase 4D, Step 18).
#
# Credentials NEVER appear in this file: libpq reads them from the
# environment (PGPASSWORD or a .pgpass file) when host binaries are used, or
# from local socket trust inside the container when the docker overrides
# below are used. Nothing here uploads anywhere; the destination is a local
# directory you control (rotate it off-host yourself — see DEPLOYMENT.md).
#
# Required environment:
#   PGHOST  PGUSER  PGDATABASE
# Optional environment:
#   PGPORT           (default 5432)
#   BACKUP_DIR       (default: ./backups)
#   BACKUP_KEEP       (retention: keep the newest N dumps here; default 14)
#   GPG_RECIPIENT    (when set, output is encrypted: gpg -e -r <recipient>)
#   PG_DUMP_BIN      (default pg_dump; may CONTAIN ARGUMENTS, e.g.
#                     'docker exec -i <container> pg_dump' — the value is
#                     intentionally word-split)
#
# Example (container client against the local instance):
#   PG_DUMP_BIN='docker exec -i sparkprompt-postgres pg_dump' \
#   PGHOST=/var/run/postgresql PGUSER=sparkprompt PGDATABASE=sparkprompt \
#   ./scripts/backup-db.sh
set -eu

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${BACKUP_DIR:-$PWD/backups}"
PGPORT="${PGPORT:-5432}"
PG_DUMP_BIN="${PG_DUMP_BIN:-pg_dump}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"

: "${PGHOST:?PGHOST is required}"
: "${PGUSER:?PGUSER is required}"
: "${PGDATABASE:?PGDATABASE is required}"

mkdir -p "$BACKUP_DIR"
OUT="$BACKUP_DIR/${PGDATABASE}_${STAMP}.dump"

echo "backup: $PGDATABASE at $PGHOST:$PGPORT -> $OUT (credentials from environment only)"

if [ -n "${GPG_RECIPIENT:-}" ]; then
  # Encrypted at rest: the plaintext dump never touches the destination.
  # shellcheck disable=SC2086  # command prefix may contain arguments
  $PG_DUMP_BIN -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -Fc \
    | gpg --batch --yes --trust-model always -r "$GPG_RECIPIENT" -e -o "$OUT.gpg"
  OUT="$OUT.gpg"
else
  # stdout is redirected HERE (not via pg_dump's -f) so container-based
  # clients (docker exec wrappers) work identically to host binaries.
  # shellcheck disable=SC2086  # command prefix may contain arguments
  $PG_DUMP_BIN -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -Fc > "$OUT"
fi

# Retention: prune the OLDEST dumps in BACKUP_DIR only (never anywhere else).
if [ -n "$BACKUP_KEEP" ] && [ "$BACKUP_KEEP" -gt 0 ] 2>/dev/null; then
  # shellcheck disable=SC2012  # dump names contain no whitespace
  ls -1t "$BACKUP_DIR"/*.dump "$BACKUP_DIR"/*.dump.gpg 2>/dev/null \
    | tail -n "+$((BACKUP_KEEP + 1))" \
    | while IFS= read -r old; do
        echo "backup: pruning old dump $(basename "$old")"
        rm -f "$old"
      done
fi

echo "backup: OK $(basename "$OUT")"
