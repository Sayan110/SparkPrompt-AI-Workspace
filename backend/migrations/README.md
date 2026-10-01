# SparkPrompt Migrations (Phase 4B)

Alembic migrations are the **single source of truth** for the SparkPrompt
schema. The application never creates, alters, or patches schema at startup —
it verifies the schema and fails clearly if it is unmigrated or stale.

```
Postgres
  ↓  alembic upgrade head          (deployment/startup orchestration)
FastAPI                            (verify_schema_ready() — read-only)
  ↓
application
```

## Layout

```
backend/
  alembic.ini            # configuration — NO database URL, NO credentials
  migrations/
    env.py               # reads the URL from application settings (env/.env)
    script.py.mako       # template for new revisions
    versions/
      0001_baseline_sparkprompt_schema.py     # schema as found at adoption (IRREVERSIBLE baseline)
      0002_add_prompt_run_version_foreign_key.py  # prompt_runs.version_id FK (reversible)
```

## Commands (run from `backend/`)

```bash
.venv-linux/bin/alembic heads                 # exactly one head: 0002
.venv-linux/bin/alembic history               # readable revision chain
.venv-linux/bin/alembic current               # revision of the connected DB
.venv-linux/bin/alembic upgrade head          # apply all pending migrations (no-op at head)
.venv-linux/bin/alembic upgrade --sql head    # print SQL without connecting
.venv-linux/bin/alembic check                 # model-vs-database drift check
.venv-linux/bin/alembic downgrade 0001        # drops ONLY the 0002 FK constraint (data-safe)
```

The database URL always comes from application settings
(`DATABASE_URL` environment variable or `.env`) — never from this repository.

## How the populated database was reconciled (adoption)

1. `alembic stamp 0001` — records that the existing schema is the baseline.
   Purely metadata: no table, column, or row was touched.
2. `alembic upgrade head` — applies only 0002 (guarded FK addition).

Fresh databases run the same chain from zero: 0001 creates the six tables,
0002 adds the FK. Both paths end at an identical schema.

## Creating a new migration

1. Change the SQLAlchemy models (`backend/app/models/`) first.
2. `alembic revision --autogenerate -m "describe the actual change"`
   (runs against the dev database; review the generated file line by line —
   autogenerate is a starting point, not an authority).
3. Run `alembic check` — it must report no unexpected drift after applying.
4. Run the migration suite: `pytest tests/test_migrations.py`.
5. Never edit an already-applied revision; add a new one.

## Safety rules

- Never run destructive SQL (`DROP TABLE`, `TRUNCATE`, volume removals)
  against the persistent development database.
- Migrations must refuse to run rather than delete or rewrite data that
  violates a new constraint (see the guard in 0002).
- Legacy `prompt_runs.version_id` values that are NULL stay NULL forever —
  identities are never backfilled from matching bodies.
- The baseline (0001) downgrade is intentionally unimplemented: dropping the
  populated tables is irreversible by design.

## Startup integration

`scripts/backend-on.sh` runs `alembic upgrade head` before starting uvicorn,
so local startup follows the diagram above. ON.bat/OFF.bat are unchanged —
they call the script and do not embed migration logic.

If the API starts without the orchestration step (e.g. a bare uvicorn), its
lifespan calls `verify_schema_ready()` and aborts with a clear error instead
of modifying the database.
