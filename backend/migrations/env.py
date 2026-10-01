"""Alembic environment for SparkPrompt (Phase 4B).

Rules:
- The database URL comes from the application settings (environment / .env).
  Nothing here or in alembic.ini stores credentials.
- ``target_metadata`` is the application's own SQLAlchemy metadata, so
  ``alembic check`` / autogenerate compares the real models against the real
  database and reports drift instead of guessing.
- An explicit ``sqlalchemy.url`` (used by tests and one-off tooling) always
  wins over the settings default.
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv

load_dotenv(BACKEND_DIR.parent / ".env")
load_dotenv(BACKEND_DIR / ".env")

from app import models  # noqa: F401  (registers every table on Base.metadata)
from app.core.config import get_settings
from app.core.database import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option(
        "sqlalchemy.url",
        get_settings().database_url.replace("%", "%%"),
    )

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit migration SQL without connecting (``alembic upgrade --sql``)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live connection."""
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure_and_run(connection)
        return
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as live_connection:
        _configure_and_run(live_connection)


def _configure_and_run(connection) -> None:
    # compare_server_default stays at its default (off): the models declare
    # func.now() where PostgreSQL stores now(), and textual default comparison
    # would report that equivalent spelling as drift. Documented in Phase 4B
    # report section 13.
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
