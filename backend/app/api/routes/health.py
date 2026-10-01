import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.database import engine, expected_schema_revision

router = APIRouter()

logger = logging.getLogger(__name__)

# Phase 4E: last observed database-check state for transition logging. It
# starts "ok" because the lifespan verified connectivity before this process
# serves traffic, so a failure on the first probe is a genuine OK -> unavailable
# transition. While the state is unchanged, readiness logs NOTHING — a
# 15-second probe interval must never turn into log spam; each transition is
# reported exactly once (ERROR down, INFO on recovery).
_database_ok = True


def _observe_database_check(ok: bool) -> None:
    global _database_ok
    previous = _database_ok
    _database_ok = ok
    if ok == previous:
        return
    if ok:
        logger.info("readiness: database check recovered (unavailable -> ok)")
    else:
        # No exception detail here on purpose: readiness swallows the original
        # exception (it must never raise), and connection errors can carry
        # connection-string fragments.
        logger.error("readiness: database check transitioned (ok -> unavailable)")


def health() -> dict[str, str]:
    """Liveness: the process is up and serving HTTP (unchanged, Phase 4A/4B)."""
    return {"status": "ok", "service": "sparkprompt-api"}


def readiness() -> JSONResponse:
    """Readiness: can this process serve traffic? (Phase 4D, Step 17).

    Verifies PostgreSQL is reachable and the schema is at the expected Alembic
    head — read-only, two cheap queries, and deliberately independent of any
    AI provider. The response never includes connection strings, credentials,
    or secret configuration; only check names, statuses, and the applied
    migration revision.
    """
    checks: dict[str, str] = {}
    revision: str | None = None

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001 - readiness must never raise
        checks["database"] = "unavailable"

    _observe_database_check(checks["database"] == "ok")

    if checks["database"] == "ok":
        try:
            with engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
        except Exception:  # noqa: BLE001 - table missing counts as unknown state
            revision = None
        try:
            expected = expected_schema_revision()
        except Exception:  # noqa: BLE001 - migration files missing in image
            expected = None
        if revision is None or expected is None:
            checks["migrations"] = "unknown"
        elif revision == expected:
            checks["migrations"] = "ok"
        else:
            checks["migrations"] = "stale"
    else:
        checks["migrations"] = "unknown"

    ready = all(status == "ok" for status in checks.values())
    body: dict[str, object] = {
        "status": "ok" if ready else "unavailable",
        "service": "sparkprompt-api",
        "checks": checks,
    }
    if revision is not None:
        body["revision"] = revision
    return JSONResponse(status_code=200 if ready else 503, content=body)


router.add_api_route("", health, methods=["GET"])
router.add_api_route("/", health, methods=["GET"])
router.add_api_route("/ready", readiness, methods=["GET"])
