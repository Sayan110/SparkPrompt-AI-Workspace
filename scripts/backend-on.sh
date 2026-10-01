#!/usr/bin/env bash
# backend-on.sh -- start the FastAPI backend in WSL, detached, idempotent.
# Usage (from Windows PowerShell):
#   wsl -d Ubuntu -- bash /mnt/c/.../SparkPrompt-AI-Workspace/scripts/backend-on.sh
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
APP_DIR="$PROJECT_DIR/backend"
PY="$APP_DIR/.venv-linux/bin/python"
LOG="/tmp/sparkprompt-uvicorn.log"
PIDFILE="/tmp/sparkprompt-uvicorn.pid"

# Skip if already healthy.
if "$PY" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)" >/dev/null 2>&1; then
  echo "backend: already healthy (skip)"
  exit 0
fi

# Do not start on top of a port 8000 already held by something else.
if command -v ss >/dev/null 2>&1; then
  HELD=$(ss -ltnp 2>/dev/null | awk '/:8000 /{print $6}' | head -1)
  if [ -n "$HELD" ]; then
    echo "backend: WARNING port 8000 already in use ($HELD); not starting a second instance."
    exit 1
  fi
fi

cd "$APP_DIR" || { echo "backend: cannot cd $APP_DIR"; exit 1; }

# Phase 4E (P7): rotate the previous boot's log — ONE generation only — before
# this boot's Alembic output lands, so the prior cycle's migration evidence
# stays inspectable in $LOG.prev. The frozen ON.bat hint keeps pointing at
# $LOG itself; the path never moves.
if [ -f "$LOG" ]; then
  mv -f "$LOG" "$LOG.prev"
fi

# Phase 4B: migrations are the schema source of truth. Apply them explicitly
# as part of startup orchestration (no-op when already at head) BEFORE the
# API starts. The application itself never mutates schema at runtime and will
# refuse to start against an unmigrated/stale database.
echo "backend: alembic upgrade head..."
if ! "$APP_DIR/.venv-linux/bin/alembic" -c "$APP_DIR/alembic.ini" upgrade head >>"$LOG" 2>&1; then
  echo "backend: ERROR migration failed; not starting API (see $LOG)"
  exit 1
fi

echo "backend: starting FastAPI (detached)..."
# Phase 4E (P2): --no-access-log disables uvicorn's built-in access lines —
# the application's own correlated "request" record (app.access, with
# X-Request-ID and duration) is the single access log per request. Alembic's
# output above is APPENDED too: it must survive in the same file (no
# truncation between migration and server output).
setsid nohup "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log >>"$LOG" 2>&1 < /dev/null &
echo $! > "$PIDFILE"

sleep 1
if "$PY" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)" >/dev/null 2>&1; then
  echo "backend: started, health OK (pid $(cat "$PIDFILE"))"
else
  echo "backend: started but health not yet OK; watching log: tail -f $LOG"
fi