#!/usr/bin/env bash
# backend-health.sh -- report backend health for the ON/OFF scripts.
# Exit 0 and print 'healthy' when /api/health responds ok, else 1 and 'down'.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PY="$PROJECT_DIR/backend/.venv-linux/bin/python"

if "$PY" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" >/dev/null 2>&1; then
  echo "healthy"
  exit 0
fi
echo "down"
exit 1