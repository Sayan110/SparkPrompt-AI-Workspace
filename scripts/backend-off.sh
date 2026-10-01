#!/usr/bin/env bash
# backend-off.sh -- stop the FastAPI backend in WSL, detached, idempotent.
# Scoped: only the process from the pid file, the port-8000 listener, or a
# uvicorn matching 'uvicorn app.main:app' in this project.
set -u

PIDFILE="/tmp/sparkprompt-uvicorn.pid"
STOPPED=0

# 1) Pid file from backend-on.sh
if [ -f "$PIDFILE" ]; then
  PID="$(cat "$PIDFILE")"
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    echo "backend: stopping (pid $PID)..."
    kill "$PID" 2>/dev/null
    sleep 1
    kill -9 "$PID" 2>/dev/null || true
    STOPPED=1
  fi
  rm -f "$PIDFILE"
fi

# 2) Port-8000 listener, parsed from ss
if command -v ss >/dev/null 2>&1; then
  PORT_PID=$(ss -ltnp 2>/dev/null | awk '/:8000 /{gsub(/.*pid=/,"",$6); gsub(/,.*/,"",$6); print $6}' | head -1)
  if [ -n "$PORT_PID" ] && [ "$PORT_PID" != "-" ]; then
    echo "backend: stopping listener (pid $PORT_PID)..."
    kill "$PORT_PID" 2>/dev/null || true
    STOPPED=1
  fi
fi

# 3) Scope check against this project's uvicorn only
P=$(pgrep -f "uvicorn app.main:app" 2>/dev/null | head -1)
if [ -n "$P" ]; then
  echo "backend: stopping uvicorn (pid $P)..."
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  STOPPED=1
fi

if [ "$STOPPED" -eq 0 ]; then
  echo "backend: nothing running (skip)"
else
  rm -f "$PIDFILE"
  echo "backend: stopped"
fi