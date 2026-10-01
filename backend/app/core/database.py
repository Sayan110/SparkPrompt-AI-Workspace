from collections.abc import Generator
from functools import lru_cache
from pathlib import Path

# SQLAlchemy imports kept here so every database-related module uses one source.
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
# Phase 4E: hide_parameters=True redacts bound values from SQLAlchemy error
# messages/tracebacks — prompt content and credentials can never surface in a
# log record or an unexpected-500 traceback. No behavior change otherwise.
engine = create_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

# backend/alembic.ini -- migrations are the schema source of truth (Phase 4B).
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@lru_cache(maxsize=1)
def expected_schema_revision() -> str:
    """Head revision declared by the migration scripts (read-only)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    if not _ALEMBIC_INI.is_file():
        raise RuntimeError(
            f"Migration configuration missing: {_ALEMBIC_INI}. "
            "Cannot determine the expected schema revision."
        )
    head = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_current_head()
    if head is None:
        raise RuntimeError("No migration head found; migrations are misconfigured.")
    return head


def verify_schema_ready(target_engine: object | None = None) -> None:
    """Fail fast when the database is not migrated (Phase 4B).

    Read-only by design: schema changes happen exclusively through versioned
    migrations applied by deployment/startup orchestration (``alembic upgrade
    head``), never at application runtime. The API refuses to start against an
    unmigrated or stale database instead of silently patching it.
    """
    active_engine = engine if target_engine is None else target_engine
    with active_engine.connect() as connection:
        has_version_table = connection.execute(
            text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
        ).scalar()
        if not has_version_table:
            raise RuntimeError(
                "Database schema is not migrated (alembic_version table missing). "
                "Run: alembic upgrade head"
            )
        current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        expected = expected_schema_revision()
        if current != expected:
            raise RuntimeError(
                f"Database schema revision {current!r} does not match the expected "
                f"head {expected!r}. Run: alembic upgrade head"
            )
        present = {
            row[0]
            for row in connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        }
        missing = sorted(set(Base.metadata.tables) - present)
        if missing:
            raise RuntimeError(
                f"Migration head {expected!r} recorded but tables missing: {missing}. "
                "Inspect the database before starting the application."
            )
