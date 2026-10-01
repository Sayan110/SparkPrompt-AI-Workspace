# syntax=docker/dockerfile:1
# SparkPrompt FastAPI — production image (Phase 4D).
#
# One controlled startup sequence (docker-entrypoint.sh), in order:
#   1. validate configuration   (fail closed; prints no secret values)
#   2. alembic upgrade head     (migrations before application, exactly once)
#   3. exec uvicorn             (single worker by default — see DEPLOYMENT.md:
#                                Phase 4A rate-limit and revocation state are
#                                process-local, so multi-worker is not implied)
#
# Test-only tooling is intentionally absent: requirements-dev.txt and tests/
# are not part of the build context (.dockerignore).
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && useradd --system --create-home --uid 10001 sparkprompt \
    && chown -R sparkprompt:sparkprompt /app

USER sparkprompt

EXPOSE 8000

# Liveness only (cheap). Readiness — DB reachable + Alembic at head — lives at
# GET /api/health/ready and is consumed by the proxy / orchestrator.
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

ENTRYPOINT ["docker-entrypoint.sh"]
