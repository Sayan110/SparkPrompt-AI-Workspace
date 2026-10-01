"""Phase 4B — migration discipline tests.

Covers the 4B Step 19 minimum without ever touching the project's persistent
Docker volume or assuming the development database is disposable:

- Alembic configuration loads and every revision imports
- exactly one head; meaningful revision messages
- baseline downgrade is explicitly irreversible (never drops data)
- the application tree contains no schema-mutating code (static policy)
- current (development) database: at head, upgrade idempotent, row counts
  stable, prompt_runs.version_id FK physically present with the model's
  on-delete behavior, legacy NULL version IDs preserved, zero invalid
  references, no sequences that could regress below existing IDs
- fresh disposable databases (created and dropped per test, separate from
  the development database): startup refuses unmigrated schemas, upgrade
  builds the full schema matching the models, upgrade is idempotent, the FK
  migration's guard refuses invalid rows without deleting anything, and the
  constraint-only downgrade is reversible

Conventions match the 3O/3P suites: integration tests carry the
``integration`` marker and skip when Postgres is unreachable.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.database import Base, expected_schema_revision, verify_schema_ready

BACKEND = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND / "alembic.ini"
TABLES = ["users", "projects", "prompts", "prompt_versions", "prompt_runs", "evaluation_records"]
CONFDEL = {"c": "CASCADE", "n": "SET NULL", "a": "NO ACTION", "r": "RESTRICT", "d": "SET DEFAULT"}


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))


def _alembic_cfg(url: str | None = None) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    if url is not None:
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def _db_reachable() -> bool:
    from sqlalchemy.exc import SQLAlchemyError

    from app.core.database import engine

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError as exc:
        print(f"  - DB unreachable, skipping integration: {exc}")
        return False


def _require_db() -> None:
    if not _db_reachable():
        pytest.skip("Postgres not reachable")


def _current_revision(conn) -> str | None:
    exists = conn.execute(
        text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
    ).scalar()
    if not exists:
        return None
    return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _schema_object_counts(engine) -> tuple[int, int, int, int]:
    """(tables, indexes, constraints, fk count) for the six product tables."""
    with engine.connect() as conn:
        tables = conn.execute(
            text(
                "SELECT count(*) FROM pg_tables WHERE schemaname='public' "
                "AND tablename = ANY(:t)"
            ),
            {"t": TABLES},
        ).scalar()
        indexes = conn.execute(
            text(
                "SELECT count(*) FROM pg_indexes WHERE schemaname='public' "
                "AND tablename = ANY(:t)"
            ),
            {"t": TABLES},
        ).scalar()
        constraints = conn.execute(
            text(
                "SELECT count(*) FROM pg_constraint WHERE connamespace='public'::regnamespace "
                "AND conrelid::regclass::text = ANY(:t)"
            ),
            {"t": TABLES},
        ).scalar()
        fks = conn.execute(
            text(
                "SELECT count(*) FROM pg_constraint WHERE connamespace='public'::regnamespace "
                "AND contype='f' AND conrelid::regclass::text = ANY(:t)"
            ),
            {"t": TABLES},
        ).scalar()
    return tables, indexes, constraints, fks


def _row_counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            t: conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar() for t in TABLES
        }


# ---------------------------------------------------------------------------
# No-database tests: configuration, revision graph, runtime policy
# ---------------------------------------------------------------------------


def test_alembic_config_loads_and_migrations_import():
    script = _script_directory()
    revisions = list(script.walk_revisions())
    assert len(revisions) == 2, "expected baseline + version-FK revisions"
    # every revision file imports cleanly and exposes callable upgrade/downgrade
    for path in sorted((BACKEND / "migrations" / "versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"_mig_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        assert callable(module.upgrade), f"{path.name}: missing upgrade()"
        assert callable(module.downgrade), f"{path.name}: missing downgrade()"
        assert getattr(module, "revision", None), f"{path.name}: missing revision id"


def test_exactly_one_migration_head():
    assert _script_directory().get_heads() == [expected_schema_revision()]


def test_migration_messages_describe_actual_changes():
    banned = {"update db", "fix schema", "changes", "misc", "wip"}
    for revision in _script_directory().walk_revisions():
        doc = (revision.doc or "").strip().lower()
        assert doc, f"revision {revision.revision} has no description"
        assert doc not in banned, f"revision {revision.revision}: vague message"


def test_baseline_downgrade_is_explicitly_irreversible():
    """The baseline refuses to drop populated tables instead of destroying data."""
    path = BACKEND / "migrations" / "versions" / "0001_baseline_sparkprompt_schema.py"
    spec = importlib.util.spec_from_file_location("mig_0001_baseline", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    with pytest.raises(NotImplementedError, match="IRREVERSIBLE"):
        module.downgrade()


def test_application_tree_contains_no_schema_mutation():
    """Runtime schema mutation is gone: only migrations may change schema."""
    forbidden = (
        "ensure_schema_updates",
        "_ensure_column",
        "create_all",
        "ALTER TABLE",
        "ADD COLUMN",
        "ADD CONSTRAINT",
        "CREATE INDEX",
        "DROP TABLE",
        "DROP COLUMN",
    )
    offenders: list[str] = []
    for path in sorted((BACKEND / "app").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in source:
                offenders.append(f"{path.relative_to(BACKEND)}: {token}")
    assert offenders == [], f"schema-mutating code found in application tree: {offenders}"


# ---------------------------------------------------------------------------
# Development database: reconciliation state (read-mostly)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_current_database_is_at_head():
    _require_db()
    from app.core.database import engine

    with engine.connect() as conn:
        current = _current_revision(conn)
    assert current == expected_schema_revision()


@pytest.mark.integration
def test_upgrade_head_is_idempotent_on_current_database():
    _require_db()
    from app.core.database import engine

    counts_before = _row_counts(engine)
    objects_before = _schema_object_counts(engine)
    with engine.connect() as conn:
        revision_before = _current_revision(conn)

    command.upgrade(_alembic_cfg(), "head")  # already at head: no-op
    command.upgrade(_alembic_cfg(), "head")  # second run must also be a no-op

    assert _row_counts(engine) == counts_before, "idempotent upgrade changed row counts"
    assert _schema_object_counts(engine) == objects_before, "idempotent upgrade changed schema objects"
    with engine.connect() as conn:
        assert _current_revision(conn) == revision_before


@pytest.mark.integration
def test_current_database_schema_object_inventory():
    _require_db()
    from app.core.database import engine

    tables, indexes, constraints, fks = _schema_object_counts(engine)
    assert tables == 6
    assert indexes == 8  # 6 pkey + ix_users_email + ix_evaluation_records_prompt_history
    assert fks == 8  # 7 baseline + prompt_runs.version_id (Phase 4B)
    assert constraints == 14  # 6 primary keys + 8 foreign keys, nothing else


@pytest.mark.integration
def test_prompt_run_version_fk_is_physical_and_matches_model():
    """The model's prompt_runs.version_id FK exists in PostgreSQL, CASCADE."""
    _require_db()
    from app.core.database import engine

    # model side
    model_fk = None
    for fk in Base.metadata.tables["prompt_runs"].foreign_keys:
        if fk.parent.name == "version_id":
            model_fk = fk
    assert model_fk is not None and model_fk.ondelete == "CASCADE"

    # database side
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid), confdeltype FROM pg_constraint "
                "WHERE conname='prompt_runs_version_id_fkey' "
                "AND conrelid='prompt_runs'::regclass"
            )).first()
    assert row is not None, "physical FK prompt_runs_version_id_fkey is missing"
    constraint_def, confdeltype = row
    assert "REFERENCES prompt_versions(id)" in constraint_def
    assert "version_id" in constraint_def
    assert CONFDEL.get(confdeltype) == model_fk.ondelete


@pytest.mark.integration
def test_null_version_ids_preserved_and_no_invalid_references():
    _require_db()
    from app.core.database import engine

    with engine.connect() as conn:
        nulls = conn.execute(
            text("SELECT count(*) FROM prompt_runs WHERE version_id IS NULL")
        ).scalar()
        linked = conn.execute(
            text("SELECT count(*) FROM prompt_runs WHERE version_id IS NOT NULL")
        ).scalar()
        invalid = conn.execute(
            text(
                "SELECT count(*) FROM prompt_runs r "
                "LEFT JOIN prompt_versions v ON v.id = r.version_id "
                "WHERE r.version_id IS NOT NULL AND v.id IS NULL"
            )
        ).scalar()
    assert nulls >= 1, "legacy NULL version IDs must be preserved (never backfilled)"
    assert linked >= 1, "version-scoped runs should exist"
    assert invalid == 0, "invalid version references are not allowed"


@pytest.mark.integration
def test_no_sequences_could_regress_below_existing_ids():
    """All PKs are client-generated UUIDs; no serial sequences to mis-order."""
    _require_db()
    from app.core.database import engine

    with engine.connect() as conn:
        sequences = conn.execute(
            text("SELECT count(*) FROM pg_sequences WHERE schemaname='public'")
        ).scalar()
        serials = 0
        for table in TABLES:
            serial = conn.execute(
                text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": table}
            ).scalar()
            serials += 1 if serial else 0
    assert sequences == 0, "unexpected sequences found; verify ID strategy"
    assert serials == 0, "id columns must stay client-generated UUIDs"


# ---------------------------------------------------------------------------
# Fresh disposable databases: build, idempotency, guard, downgrade
# ---------------------------------------------------------------------------


def _fresh_database():
    """Create a throwaway database; returns (url, teardown). Not the dev DB."""
    name = f"sparkprompt_mig_{uuid.uuid4().hex[:8]}"
    base = make_url(get_settings().database_url)
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    url = base.set(database=name).render_as_string(hide_password=False)

    def teardown() -> None:
        cleaner = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with cleaner.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        cleaner.dispose()

    return url, teardown


@pytest.mark.integration
def test_fresh_database_requires_migrations_then_starts_clean():
    _require_db()
    url, teardown = _fresh_database()
    engine = create_engine(url)
    try:
        # unmigrated: startup must fail clearly, never create schema itself
        with pytest.raises(RuntimeError, match="not migrated"):
            verify_schema_ready(engine)

        command.upgrade(_alembic_cfg(url), "head")

        verify_schema_ready(engine)  # migrated: verification passes
        tables, indexes, constraints, fks = _schema_object_counts(engine)
        assert (tables, indexes, fks) == (6, 8, 8)
        assert constraints >= 0
    finally:
        engine.dispose()
        teardown()


@pytest.mark.integration
def test_fresh_database_schema_matches_models_exactly():
    _require_db()
    url, teardown = _fresh_database()
    engine = create_engine(url)
    try:
        command.upgrade(_alembic_cfg(url), "head")

        from sqlalchemy import inspect
        from sqlalchemy.dialects import postgresql

        dialect = postgresql.dialect()

        # Rendering-only equivalences: PostgreSQL FLOAT (no precision) IS
        # double precision, so sa.Float() -> "FLOAT" while introspection of
        # the physical column returns DOUBLE PRECISION. Canonicalize both
        # spellings to one form; every other type must match exactly.
        TYPE_CANONICAL = {"FLOAT": "DOUBLE PRECISION", "DOUBLE PRECISION": "DOUBLE PRECISION"}

        def compile_type(col_type) -> str:
            compiled = str(col_type.compile(dialect=dialect)).upper()
            return TYPE_CANONICAL.get(compiled, compiled)

        inspector = inspect(engine)
        inspector_tables = set(inspector.get_table_names()) - {"alembic_version"}
        assert inspector_tables == set(TABLES)

        with engine.connect() as conn:
            # FK inventory: (table, col, reftable, refcol, ondelete)
            rows = conn.execute(text("""
                SELECT c.conrelid::regclass::text AS src, a.attname AS col,
                       c.confrelid::regclass::text AS dst, b.attname AS ref,
                       c.confdeltype
                FROM pg_constraint c
                JOIN LATERAL unnest(c.conkey) WITH ORDINALITY lk(attnum, ord) ON true
                JOIN LATERAL unnest(c.confkey) WITH ORDINALITY rk(attnum, ord) ON lk.ord = rk.ord
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = lk.attnum
                JOIN pg_attribute b ON b.attrelid = c.confrelid AND b.attnum = rk.attnum
                WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
            """)).mappings().all()
            db_fks = {(r["src"], r["col"], r["dst"], r["ref"], CONFDEL[r["confdeltype"]]) for r in rows}

            index_rows = conn.execute(text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname='public' AND tablename = ANY(:t)"
            ), {"t": TABLES}).mappings().all()
            db_indexes = {r["indexname"]: r["indexdef"] for r in index_rows}

        model_fks = set()
        for name, table in Base.metadata.tables.items():
            for fk in table.foreign_keys:
                model_fks.add(
                    (name, fk.parent.name, fk.target_fullname.split(".")[0],
                     fk.target_fullname.split(".")[1], fk.ondelete)
                )
        assert db_fks == model_fks, f"FK drift:\n db-only={db_fks - model_fks}\n model-only={model_fks - db_fks}"

        for name, table in Base.metadata.tables.items():
            columns = inspector.get_columns(name)
            by_name = {c["name"]: c for c in columns}
            assert set(by_name) == {c.name for c in table.columns}, f"{name}: column set drift"
            for col in table.columns:
                db_col = by_name[col.name]
                assert compile_type(col.type) == compile_type(db_col["type"]), (
                    f"{name}.{col.name}: type drift {col.type} vs {db_col['type']}"
                )
                assert bool(col.nullable) == bool(db_col["nullable"]), (
                    f"{name}.{col.name}: nullability drift"
                )
            db_pk = inspector.get_pk_constraint(name)["constrained_columns"]
            assert set(db_pk) == {c.name for c in table.primary_key.columns}

        # secondary indexes: name + uniqueness + DESC ordering for history reads
        model_index_names = set()
        for table in Base.metadata.tables.values():
            for index in table.indexes:
                model_index_names.add(index.name)
        assert {n for n in db_indexes if not n.endswith("_pkey")} == model_index_names
        assert len(db_indexes) == 8
        history_def = db_indexes["ix_evaluation_records_prompt_history"]
        assert "created_at DESC" in history_def and "id DESC" in history_def
    finally:
        engine.dispose()
        teardown()


@pytest.mark.integration
def test_fresh_upgrade_head_is_idempotent():
    _require_db()
    url, teardown = _fresh_database()
    engine = create_engine(url)
    try:
        command.upgrade(_alembic_cfg(url), "head")
        before = (_schema_object_counts(engine), _row_counts(engine))
        with engine.connect() as conn:
            revision_before = _current_revision(conn)

        command.upgrade(_alembic_cfg(url), "head")

        assert (_schema_object_counts(engine), _row_counts(engine)) == before
        with engine.connect() as conn:
            assert _current_revision(conn) == revision_before == expected_schema_revision()
    finally:
        engine.dispose()
        teardown()


@pytest.mark.integration
def test_version_fk_guard_refuses_invalid_rows_without_deleting():
    """0002 must STOP on violating rows: no deletion, no rewrite, no FK."""
    _require_db()
    url, teardown = _fresh_database()
    engine = create_engine(url)
    try:
        command.upgrade(_alembic_cfg(url), "0001")  # schema without the FK

        bogus_version = str(uuid.uuid4())
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO users (id, email, display_name)
                VALUES ('00000000-0000-4000-8000-000000000001', 'guard@example.test', 'Guard')
            """))
            conn.execute(text("""
                INSERT INTO projects (id, user_id, name)
                VALUES ('00000000-0000-4000-8000-000000000002',
                        '00000000-0000-4000-8000-000000000001', 'guard-project')
            """))
            conn.execute(text("""
                INSERT INTO prompts (id, project_id, title, idea, audience, output_format, depth)
                VALUES ('00000000-0000-4000-8000-000000000003',
                        '00000000-0000-4000-8000-000000000002',
                        'guard-prompt', 'guard idea', 'everyone', 'best', 2)
            """))
            conn.execute(text(f"""
                INSERT INTO prompt_runs (id, prompt_id, status, version_id)
                VALUES ('00000000-0000-4000-8000-000000000004',
                        '00000000-0000-4000-8000-000000000003', 'error', '{bogus_version}')
            """))

        with pytest.raises(RuntimeError, match="Refusing to add prompt_runs.version_id"):
            command.upgrade(_alembic_cfg(url), "head")

        with engine.connect() as conn:
            # the violating row must still exist: guard never deletes
            still_there = conn.execute(text(
                "SELECT count(*) FROM prompt_runs WHERE id = '00000000-0000-4000-8000-000000000004'"
            )).scalar()
            revision = _current_revision(conn)
            fk_exists = conn.execute(text(
                "SELECT count(*) FROM pg_constraint WHERE conname='prompt_runs_version_id_fkey'"
            )).scalar()
        assert still_there == 1, "guard must never delete data"
        assert revision == "0001", "failed migration must roll back"
        assert fk_exists == 0, "FK must not exist after refused upgrade"

        # legacy NULL path: clearing the identity (the only legitimate fix)
        # lets the migration proceed; the row survives with NULL preserved.
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE prompt_runs SET version_id = NULL "
                "WHERE id = '00000000-0000-4000-8000-000000000004'"
            ))
        command.upgrade(_alembic_cfg(url), "head")
        with engine.connect() as conn:
            survivor = conn.execute(text(
                "SELECT version_id FROM prompt_runs "
                "WHERE id = '00000000-0000-4000-8000-000000000004'"
            )).scalar()
            assert survivor is None
            assert _current_revision(conn) == expected_schema_revision()
    finally:
        engine.dispose()
        teardown()


@pytest.mark.integration
def test_downgrade_removes_only_the_fk_and_is_reversible():
    """0002 downgrade: constraint-only, data-safe; startup catches stale schema."""
    _require_db()
    url, teardown = _fresh_database()
    engine = create_engine(url)
    try:
        command.upgrade(_alembic_cfg(url), "head")

        command.downgrade(_alembic_cfg(url), "0001")
        with engine.connect() as conn:
            fk = conn.execute(text(
                "SELECT count(*) FROM pg_constraint WHERE conname='prompt_runs_version_id_fkey'"
            )).scalar()
            tables = conn.execute(text(
                "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename = ANY(:t)"
            ), {"t": TABLES}).scalar()
            revision = _current_revision(conn)
        assert fk == 0, "downgrade must drop only the constraint"
        assert tables == 6, "downgrade must not drop any table"
        assert revision == "0001"

        # stale-but-valid schema: startup must fail clearly (not patch itself)
        with pytest.raises(RuntimeError, match="does not match the expected head"):
            verify_schema_ready(engine)

        command.upgrade(_alembic_cfg(url), "head")
        with engine.connect() as conn:
            fk = conn.execute(text(
                "SELECT count(*) FROM pg_constraint WHERE conname='prompt_runs_version_id_fkey'"
            )).scalar()
            assert fk == 1
        verify_schema_ready(engine)
    finally:
        engine.dispose()
        teardown()
