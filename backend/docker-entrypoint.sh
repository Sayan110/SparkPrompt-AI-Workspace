#!/bin/sh
# SparkPrompt production startup (Phase 4D, Steps 12/25).
#
# Order is deliberate: configuration is verified BEFORE anything touches the
# database, migrations run exactly once here — before uvicorn, in a single
# container instance, so workers can never race Alembic — then the app is
# exec'd (PID 1) for clean SIGTERM shutdown.
#
# Error messages are actionable and never include secret values.
set -eu

echo "sparkprompt: validating configuration (fail closed)..."
python -m app.core.config_check --require production

echo "sparkprompt: applying migrations (alembic upgrade head)..."
alembic -c alembic.ini upgrade head

# Trust X-Forwarded-Proto/Host/For only from the reverse proxy (Step 16):
# resolve the compose service "proxy" to its IP when present, otherwise fall
# back to loopback. Never "*": an unrestricted allow-list would let any peer
# that can reach :8000 forge client IPs and forwarded protocol headers.
if [ -z "${FORWARDED_ALLOW_IPS:-}" ]; then
  PROXY_IP="$(getent hosts proxy 2>/dev/null | awk '{print $1}' | head -n 1 || true)"
  FORWARDED_ALLOW_IPS="${PROXY_IP:-127.0.0.1}"
fi

echo "sparkprompt: starting uvicorn (host=${API_HOST:-0.0.0.0} port=${API_PORT:-8000} workers=${WEB_CONCURRENCY:-1})..."
# Phase 4E: --no-access-log — the application's correlated "request" record
# (app.access with X-Request-ID, route template, duration) is the single
# access log per request; uvicorn's own access lines are redundant noise.
exec python -m uvicorn app.main:app \
  --host "${API_HOST:-0.0.0.0}" \
  --port "${API_PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-1}" \
  --proxy-headers \
  --no-access-log \
  --forwarded-allow-ips "$FORWARDED_ALLOW_IPS"
