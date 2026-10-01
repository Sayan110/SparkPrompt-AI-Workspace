from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
import logging
import re
import time
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.requests import Request
from starlette.responses import Response

from app.api import api_router
from app.api.routes.common import not_authenticated_handler, not_found_handler
from app.core.config import get_settings
from app.core.database import (
    SessionLocal,
    engine,
    expected_schema_revision,
    verify_schema_ready,
)
from app.core.logging import configure_logging, request_id_ctx
from app.services.demo_user import ensure_demo_workspace
from app.services.errors import NotAuthenticatedError, NotFoundError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Phase 4E: logging is configured FIRST — before any validation below — so
    # startup failures are visible as timestamped, structured records (JSON in
    # production) instead of an unformatted traceback. Invalid LOG_LEVEL cannot
    # reach here: Settings rejects it at construction.
    configure_logging(settings.log_level, json_format=settings.is_production)
    # Phase 4D: configuration is obtained (env / .env) at Settings load and is
    # verified FIRST — production startup fails closed with actionable messages
    # before anything else happens (no silent development fallbacks).
    # Phase 4B: startup verifies the schema, it never mutates it. Migrations
    # are applied by orchestration (scripts/backend-on.sh and the production
    # entrypoint run "alembic upgrade head" before uvicorn); a stale or missing
    # schema makes startup fail with a clear message instead of silently
    # patching tables.
    try:
        settings.validate_for_startup()
        verify_schema_ready()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        db = SessionLocal()
        try:
            ensure_demo_workspace(db)
        finally:
            db.close()
    except Exception:
        # Phase 4E: startup failures log ERROR with the traceback and re-raise
        # — fail-fast semantics are unchanged, only visibility improves.
        logger.exception("startup failed")
        raise
    logger.info(
        "startup complete: configuration validated, database reachable, "
        "schema revision %s verified, demo workspace ready",
        expected_schema_revision(),
    )
    yield
    logger.info("shutdown complete")


settings = get_settings()
app = FastAPI(
    title="SparkPrompt API",
    version="0.1.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Minimal security headers (Phase 4A): applied to every response.

    Deliberately minimal — CSP and HSTS are deployment concerns and belong to
    Phase 4D (they need real origin/TLS decisions). ``setdefault`` never
    overwrites a header a handler or CORS middleware already set.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


# ---------------------------------------------------------------- Phase 4E --
# Request correlation + timing + the single application access-log source.
# Registered AFTER security_headers so it is the OUTERMOST user middleware:
# it observes every response (CORS, 404s, exception-generated 500s) exactly
# once, and uvicorn's own access log is disabled in both lifecycle entrypoints
# (scripts/backend-on.sh, backend/docker-entrypoint.sh) so this is the only
# access record per request. The frozen E2E third path
# (frontend/e2e/scripts/start-api.mjs) intentionally keeps uvicorn access
# logging — documented in DEPLOYMENT.md §14.

_request_id_pattern = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
access_logger = logging.getLogger("app.access")


def _resolve_request_id(inbound: str | None) -> str:
    """Trust an inbound X-Request-ID only when it is a safe token.

    nginx supplies ``$request_id`` (32 hex chars — always matches); anything
    malformed or oversized is replaced with a fresh UUID4 so a hostile header
    can never inject content into the log stream.
    """
    if inbound and _request_id_pattern.match(inbound):
        return inbound
    return str(uuid.uuid4())


def _route_path(request: Request) -> str:
    """Route template when FastAPI matched one, else the bare path (no query)."""
    template = getattr(request.scope.get("route"), "path", None)
    return template if isinstance(template, str) and template else request.url.path


def _client_ip(request: Request) -> str:
    # Socket peer address only — mirrors app.api.deps._client_key. Behind the
    # proxy this is the real client (uvicorn trusts X-Forwarded-For solely from
    # the proxy via --forwarded-allow-ips); raw X-Forwarded-For is never read here.
    client = request.client
    return client.host if client is not None and client.host else "unknown"


def _request_user_id(request: Request) -> str | None:
    """Internal UUID stamped by app.api.deps.get_current_user, or None (anonymous)."""
    value = getattr(request.state, "user_id", None)
    return str(value) if value is not None else None


def _access_level(status_code: int, request: Request) -> int:
    if request.url.path.startswith("/api/health"):
        # Health probes are high-frequency (every 15s): access records stay at
        # DEBUG so readiness polls add no INFO noise; readiness state
        # transitions are reported once by app.api.routes.health itself.
        return logging.DEBUG
    if status_code >= 500:
        return logging.ERROR
    if status_code >= 400:
        return logging.WARNING
    return logging.INFO


@app.middleware("http")
async def request_observability(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Correlate, time and log every request — privacy-safe by construction.

    Emits exactly ONE ``request`` access record per request (method, route
    template, status, duration_ms, client_ip, and the internal user UUID when
    authenticated) at INFO/WARNING/ERROR for 2xx/4xx/5xx (DEBUG for health
    probes), plus one ERROR record with traceback when an exception escapes
    the route — logged, then re-raised so FastAPI's generic 500 behavior is
    untouched. Never logs query strings, bodies, cookies, Authorization,
    emails, prompt text or AI output. Exception-generated 500 responses are
    produced by Starlette's outermost ServerErrorMiddleware, so they carry no
    X-Request-ID header — correlation for those goes through the log record's
    request_id (documented limitation, DEPLOYMENT.md §14).
    """
    request_id = _resolve_request_id(request.headers.get("X-Request-ID"))
    ctx_token = request_id_ctx.set(request_id)
    started = time.perf_counter()
    status_code = 500  # only reachable as the final status when call_next raised
    try:
        response = await call_next(request)
        status_code = response.status_code
        # Preserve a valid inbound id (nginx's $request_id) or answer ours.
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception as exc:  # noqa: BLE001 - log context, then re-raise
        access_logger.error(
            "request failed: %s",
            type(exc).__name__,
            exc_info=True,
            extra={"route": _route_path(request), "client_ip": _client_ip(request)},
        )
        raise
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        fields: dict[str, object] = {
            "method": request.method,
            "route": _route_path(request),
            "status": status_code,
            "duration_ms": duration_ms,
            "client_ip": _client_ip(request),
        }
        user_id = _request_user_id(request)
        if user_id is not None:
            fields["user_id"] = user_id
        access_logger.log(_access_level(status_code, request), "request", extra=fields)
        request_id_ctx.reset(ctx_token)


app.add_exception_handler(NotFoundError, not_found_handler)
app.add_exception_handler(NotAuthenticatedError, not_authenticated_handler)

app.include_router(api_router, prefix="/api")
