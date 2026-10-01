"""Phase 4F fixture — insert ONE deterministic PromptRun into the E2E database.

Why this exists: a PromptRun can only be created through the AI gateway, which
requires a live provider. Phase 4F tests the AI boundary honestly instead of
faking provider behavior, but evaluation history / comparison persistence still
need *some* run to anchor to. This script writes exactly one run row for one
owned prompt — via the application's own ORM, no schema changes — so the real
evaluation + history + comparison code paths can be exercised end to end.

Scope guard: refuses to run unless DATABASE_URL targets sparkprompt_e2e.
The persistent sparkprompt database can never be reached from here.

Usage (inside WSL, from the repository's backend directory):

    DATABASE_URL=postgresql+psycopg://sparkprompt:sparkprompt@127.0.0.1:5432/sparkprompt_e2e \
        .venv-linux/bin/python <repo>/frontend/e2e/scripts/seed-run.py <prompt_id>

Prints the new run's UUID on the last line of stdout.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

EXPECTED_DB = "sparkprompt_e2e"

# Fixed fixture output. Evaluation rules in the suite are written against it:
# contains "Test" (case-insensitive) → PASS; contains "Absent" → FAIL.
FIXTURE_OUTPUT = (
    "E2E fixture output, line one.\n"
    "Test sentence that satisfies the contains rule.\n"
    "Deterministic text used by the Phase 4F evaluation journey."
)


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(2)


database_url = os.environ.get("DATABASE_URL", "")
if f"/{EXPECTED_DB}" not in database_url:
    fail(f"Refusing: DATABASE_URL must target /{EXPECTED_DB} (got: {database_url!r}).")
if len(sys.argv) != 2:
    fail("usage: seed-run.py <prompt_id>")

backend_dir = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(backend_dir))

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models import Prompt, PromptRun, PromptVersion  # noqa: E402

try:
    prompt_id = uuid.UUID(sys.argv[1])
except ValueError:
    fail(f"prompt_id is not a UUID: {sys.argv[1]!r}")

db = SessionLocal()
try:
    prompt = db.get(Prompt, prompt_id)
    if prompt is None:
        fail(f"Prompt {prompt_id} not found in {EXPECTED_DB}.")

    version = db.scalar(
        select(PromptVersion)
        .where(PromptVersion.prompt_id == prompt_id)
        .order_by(PromptVersion.version_number.desc())
        .limit(1)
    )

    run = PromptRun(
        id=uuid.uuid4(),
        prompt_id=prompt.id,
        version_id=version.id if version is not None else None,
        provider="e2e-fixture",
        model="e2e-fixture",
        # Prompt.body does not exist — the body lives on PromptVersion rows.
        input_snapshot={
            "messages": [
                {
                    "role": "user",
                    "content": version.body
                    if version is not None
                    else (prompt.idea or ""),
                }
            ]
        },
        output_text=FIXTURE_OUTPUT,
        status="succeeded",
        finish_reason="stop",
        latency_ms=1,
        usage_json={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )
    db.add(run)
    db.commit()
    print(run.id)
finally:
    db.close()
