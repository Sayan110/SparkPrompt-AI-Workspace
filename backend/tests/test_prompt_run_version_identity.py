"""Phase 3O PromptRun version-identity tests.

Every persisted PromptRun for a version-scoped execution must identify the
exact immutable PromptVersion executed — server-resolved, ownership-checked,
immutable, persisted. Covers the Step 30 matrix (model 1-4, testing 5-10,
ownership 11-15, versioning 16-20, experiments 21-23, evaluation 24,
database 33-35). Phase regressions 25-32 are proven by the full suite run.

Conventions match the 3I/3J suites: ``integration`` marker, module-scoped
``db_ready`` reachability skip, plain ``TestClient`` plus the conftest
``app_client`` (fake provider), direct ``SessionLocal`` reads for stored-row
claims.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal, engine
from app.models import Prompt, PromptRun, PromptVersion

pytestmark = pytest.mark.integration


def _db_reachable() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(select(1))
        return True
    except SQLAlchemyError as exc:
        print(f"  - DB unreachable, skipping integration: {exc}")
        return False


@pytest.fixture(scope="module")
def db_ready() -> bool:
    return _db_reachable()


@pytest.fixture(scope="module", autouse=True)
def _verify_schema(db_ready) -> None:
    """Verify the migrated schema (read-only) so DB reads never depend on test order.

    Phase 4B: application code no longer creates or patches schema at startup
    (migrations own that); tests only verify the database is migrated.
    """
    _require_db(db_ready)
    from app.core.database import verify_schema_ready

    verify_schema_ready()


def _require_db(db_ready: bool) -> None:
    if not db_ready:
        pytest.skip("Postgres not reachable")


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _create_prompt(client, body: str, **overrides) -> dict:
    payload = {
        "title": overrides.pop("title", f"3O version identity {uuid.uuid4().hex[:8]}"),
        "idea": overrides.pop("idea", "Prove runs identify exact versions."),
        "body": body,
    }
    payload.update(overrides)
    response = client.post("/api/prompts", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _history(client, prompt_id: str) -> list[dict]:
    response = client.get(f"/api/prompts/{prompt_id}/versions")
    assert response.status_code == 200, response.text
    return response.json()


def _run_row(run_id: str) -> PromptRun:
    with SessionLocal() as session:
        run = session.get(PromptRun, uuid.UUID(run_id))
        assert run is not None
        session.expunge(run)
        return run


def _run_count() -> int:
    with SessionLocal() as session:
        return len(session.scalars(select(PromptRun)).all())


def _create_foreign_prompt_with_run() -> tuple[str, str, str]:
    """Foreign prompt + version + run. Returns (prompt_id, version_id, run_id)."""
    from app.models import Project, User

    with SessionLocal() as session:
        user = User(
            email=f"foreign-3o-{uuid.uuid4().hex[:10]}@sparkprompt.local",
            display_name="Foreign workspace owner",
        )
        session.add(user)
        session.flush()
        project = Project(user_id=user.id, name="Foreign workspace")
        session.add(project)
        session.flush()
        prompt = Prompt(project_id=project.id, title="Foreign prompt", idea="not yours")
        session.add(prompt)
        session.flush()
        version = PromptVersion(prompt_id=prompt.id, version_number=1, body="foreign body")
        session.add(version)
        session.flush()
        run = PromptRun(
            prompt_id=prompt.id, provider="fake", model="fake-model-1",
            output_text="foreign output", status="success",
        )
        session.add(run)
        session.commit()
        return str(prompt.id), str(version.id), str(run.id)


# ------------------------------------------------------- model/schema (1-4)


def test_version_id_column_is_nullable(db_ready):
    """(1) version_id exists and is nullable (historical + draft runs stay NULL)."""
    _require_db(db_ready)
    column = PromptRun.__table__.columns["version_id"]
    assert column.nullable is True


def test_version_id_foreign_key_is_valid(db_ready):
    """(2) FK targets prompt_versions.id with the project-standard CASCADE."""
    _require_db(db_ready)
    column = PromptRun.__table__.columns["version_id"]
    fks = list(column.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "prompt_versions"
    assert fks[0].column.name == "id"
    assert fks[0].ondelete == "CASCADE"


def test_historical_null_runs_read_fine(db_ready):
    """(3) pre-3O rows (version_id NULL) are ordinary readable rows."""
    _require_db(db_ready)
    with SessionLocal() as session:
        nulls = session.scalars(
            select(PromptRun).where(PromptRun.version_id.is_(None)).limit(5)
        ).all()
    assert isinstance(nulls, list)  # readable, no constraint violation


def test_version_belongs_to_prompt(app_client, db_ready):
    """(4) a versioned run's version belongs to the run's own prompt."""
    _require_db(db_ready)
    created = _create_prompt(app_client, "versioned body")
    prompt_id = created["id"]
    version_id = _history(app_client, prompt_id)[0]["id"]
    tested = app_client.post(
        "/api/testing/run",
        json={"prompt": "anything", "prompt_id": prompt_id, "version_id": version_id},
    )
    assert tested.status_code == 200, tested.text

    run = _run_row(tested.json()["run_id"])
    with SessionLocal() as session:
        version = session.get(PromptVersion, run.version_id)
    assert version is not None
    assert str(version.prompt_id) == str(run.prompt_id) == prompt_id


# ------------------------------------------------------- testing (5-10)


def test_saved_version_execution_persists_identity(app_client, db_ready):
    """(5)(6) version-scoped test persists run.version_id = the version."""
    _require_db(db_ready)
    created = _create_prompt(app_client, "saved body A")
    prompt_id = created["id"]
    version_id = _history(app_client, prompt_id)[0]["id"]
    response = app_client.post(
        "/api/testing/run",
        json={"prompt": "saved body A", "prompt_id": prompt_id, "version_id": version_id},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version_id"] == version_id
    assert body["run_id"] is not None
    assert _run_row(body["run_id"]).version_id == uuid.UUID(version_id)


def test_body_comes_from_version_not_supplied_text(app_client, db_ready):
    """(7)(8) contract A: server executes the stored body; supplied text ignored."""
    _require_db(db_ready)
    created = _create_prompt(app_client, "authoritative version body")
    prompt_id = created["id"]
    version_id = _history(app_client, prompt_id)[0]["id"]
    response = app_client.post(
        "/api/testing/run",
        json={
            "prompt": "COMPLETELY DIFFERENT DECOY TEXT",
            "prompt_id": prompt_id,
            "version_id": version_id,
        },
    )
    assert response.status_code == 200, response.text
    assert "authoritative version body" in response.json()["output"]
    assert "DECOY" not in response.json()["output"]

    run = _run_row(response.json()["run_id"])
    with SessionLocal() as session:
        messages = session.execute(
            select(PromptRun.input_snapshot).where(PromptRun.id == run.id)
        ).scalar_one()
    assert messages["messages"][0]["content"] == "authoritative version body"


def test_draft_execution_stays_versionless(app_client, db_ready):
    """(9) prompt_id without version_id persists a run with NULL identity."""
    _require_db(db_ready)
    created = _create_prompt(app_client, "saved body")
    prompt_id = created["id"]
    response = app_client.post(
        "/api/testing/run",
        json={"prompt": "edited draft resembling nothing", "prompt_id": prompt_id},
    )
    assert response.status_code == 200, response.text
    assert response.json()["version_id"] is None
    assert _run_row(response.json()["run_id"]).version_id is None


def test_arbitrary_testing_remains_valid(app_client, db_ready):
    """(10) no ids at all: unpersisted execution, unchanged behavior."""
    _require_db(db_ready)
    response = app_client.post("/api/testing/run", json={"prompt": "free text"})
    assert response.status_code == 200, response.text
    assert response.json()["run_id"] is None
    assert response.json()["version_id"] is None


def test_version_without_prompt_is_422(db_ready):
    """version_id is meaningless outside its prompt: rejected at the boundary."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, "lonely body")
        version_id = _history(client, created["id"])[0]["id"]
        response = client.post(
            "/api/testing/run", json={"prompt": "x", "version_id": version_id}
        )
    assert response.status_code == 422, response.text


# ------------------------------------------------------- ownership (11-15)


def test_foreign_prompt_rejected(app_client, db_ready):
    """(11) own version + foreign prompt: 404, no run created."""
    _require_db(db_ready)
    foreign_prompt_id, _, _ = _create_foreign_prompt_with_run()
    created = _create_prompt(app_client, "own body")
    own_version = _history(app_client, created["id"])[0]["id"]
    before = _run_count()
    response = app_client.post(
        "/api/testing/run",
        json={"prompt": "x", "prompt_id": foreign_prompt_id, "version_id": own_version},
    )
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Prompt not found"}
    assert _run_count() == before


def test_foreign_version_rejected(app_client, db_ready):
    """(12) own prompt + foreign version: safe version 404, no run, no leak."""
    _require_db(db_ready)
    _, foreign_version_id, _ = _create_foreign_prompt_with_run()
    created = _create_prompt(app_client, "own body")
    before = _run_count()
    response = app_client.post(
        "/api/testing/run",
        json={"prompt": "x", "prompt_id": created["id"], "version_id": foreign_version_id},
    )
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Prompt version not found"}
    assert "foreign body" not in response.text
    assert _run_count() == before


def test_cross_prompt_version_rejected(app_client, db_ready):
    """(13) version of owned prompt B used against owned prompt A: 404, nothing changes."""
    _require_db(db_ready)
    prompt_a = _create_prompt(app_client, "body A")["id"]
    prompt_b = _create_prompt(app_client, "body B")["id"]
    version_b = _history(app_client, prompt_b)[0]["id"]
    before = _run_count()
    response = app_client.post(
        "/api/testing/run",
        json={"prompt": "x", "prompt_id": prompt_a, "version_id": version_b},
    )
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Prompt version not found"}
    assert "body B" not in response.text
    assert _run_count() == before


def test_unknown_version_rejected(app_client, db_ready):
    """(14) version id that belongs nowhere: 404, no run."""
    _require_db(db_ready)
    created = _create_prompt(app_client, "own body")
    before = _run_count()
    response = app_client.post(
        "/api/testing/run",
        json={"prompt": "x", "prompt_id": created["id"], "version_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Prompt version not found"}
    assert _run_count() == before


def test_malicious_metadata_cannot_smuggle_version(app_client, db_ready):
    """(15) gateway never reads version identity from (client-reachable) metadata."""
    _require_db(db_ready)
    created = _create_prompt(app_client, "guarded body")
    prompt_id = created["id"]
    version_id = _history(app_client, prompt_id)[0]["id"]
    before = _run_count()

    response = app_client.post(
        "/api/ai/generate",
        json={
            "messages": [{"role": "user", "content": "smuggled execution"}],
            "metadata": {"prompt_id": prompt_id, "prompt_version_id": version_id},
        },
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    assert run_id is not None  # prompt_id path still persists (pre-existing behavior)
    assert _run_row(run_id).version_id is None  # but no version identity is smuggled
    assert _run_count() == before + 1


# ------------------------------------------------------- versioning (16-20)


def test_v1_then_v2_runs_keep_their_identities(app_client, db_ready):
    """(16)(17)(18) Save v1 -> test (v1); save v2 -> test (v2); old run stays v1."""
    _require_db(db_ready)
    created = _create_prompt(app_client, "body v1")
    prompt_id = created["id"]
    v1 = _history(app_client, prompt_id)[0]["id"]
    run1 = app_client.post(
        "/api/testing/run",
        json={"prompt": "body v1", "prompt_id": prompt_id, "version_id": v1},
    ).json()

    app_client.put(f"/api/prompts/{prompt_id}", json={"body": "body v2"})
    v2 = next(
        item["id"] for item in _history(app_client, prompt_id) if item["version_number"] == 2
    )
    run2 = app_client.post(
        "/api/testing/run",
        json={"prompt": "body v2", "prompt_id": prompt_id, "version_id": v2},
    ).json()

    assert run1["version_id"] == v1
    assert run2["version_id"] == v2
    assert _run_row(run1["run_id"]).version_id == uuid.UUID(v1)
    assert _run_row(run2["run_id"]).version_id == uuid.UUID(v2)


def test_restored_version_run_identifies_restore_not_source(app_client, db_ready):
    """(19)(20) restore v1 -> v3 (=A); testing v3 stamps v3, never v1."""
    _require_db(db_ready)
    created = _create_prompt(app_client, "body A")
    prompt_id = created["id"]
    app_client.put(f"/api/prompts/{prompt_id}", json={"body": "body B"})
    history = _history(app_client, prompt_id)
    v1 = next(item["id"] for item in history if item["version_number"] == 1)
    restored = app_client.post(f"/api/prompts/{prompt_id}/versions/{v1}/restore")
    assert restored.status_code == 201, restored.text
    v3 = restored.json()["id"]
    assert v3 != v1
    assert restored.json()["body"] == "body A"  # identical bodies, different rows

    tested = app_client.post(
        "/api/testing/run",
        json={"prompt": "body A", "prompt_id": prompt_id, "version_id": v3},
    )
    assert tested.status_code == 200, tested.text

    assert tested.json()["version_id"] == v3
    run = _run_row(tested.json()["run_id"])
    assert run.version_id == uuid.UUID(v3)
    assert run.version_id != uuid.UUID(v1)


# ------------------------------------------------------- experiments (21-23)


def test_experiment_runs_carry_exact_versions(app_client, db_ready):
    """(21)(22) every experiment run is stamped with the version evaluated."""
    _require_db(db_ready)
    bodies = ["exp body one", "exp body two", "exp body three"]
    created = _create_prompt(app_client, bodies[0])
    prompt_id = created["id"]
    for body in bodies[1:]:
        app_client.put(f"/api/prompts/{prompt_id}", json={"body": body})
    history = _history(app_client, prompt_id)
    assert [item["version_number"] for item in history] == [1, 2, 3]
    expected = [item["id"] for item in history]

    response = app_client.post(
        "/api/experiments/run",
        json={
            "prompt_id": prompt_id,
            "evaluator": {"rules": [{"type": "contains", "text": "Simulated answer"}]},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["total_versions"] == 3

    # Nothing else creates runs for a fresh prompt, so every run here is
    # experiment-created, in version evaluation order.
    with SessionLocal() as session:
        stamped = session.execute(
            select(PromptRun.version_id)
            .where(PromptRun.prompt_id == uuid.UUID(prompt_id))
            .order_by(PromptRun.created_at.asc())
        ).all()
    assert [str(row[0]) for row in stamped] == expected


def test_experiment_ordering_unchanged(app_client, db_ready):
    """(23) version ordering in experiments is exactly the pre-3O behavior."""
    _require_db(db_ready)
    created = _create_prompt(app_client, "order alpha")
    prompt_id = created["id"]
    app_client.put(f"/api/prompts/{prompt_id}", json={"body": "order beta"})
    response = app_client.post(
        "/api/experiments/run",
        json={
            "prompt_id": prompt_id,
            "evaluator": {"rules": [{"type": "contains", "text": "Simulated answer"}]},
        },
    )
    assert response.status_code == 200, response.text
    outputs = [item["output"] for item in response.json()["evaluations"]]
    assert outputs == [
        "Simulated answer to: order alpha",
        "Simulated answer to: order beta",
    ]


# ------------------------------------------------------- evaluation (24)


def test_evaluation_by_versioned_run_sees_version(app_client, db_ready):
    """(24) MODE A surfaces the run's stored version identity; MODE B echoes requested."""
    _require_db(db_ready)
    created = _create_prompt(app_client, "eval version body")
    prompt_id = created["id"]
    version_id = _history(app_client, prompt_id)[0]["id"]
    tested = app_client.post(
        "/api/testing/run",
        json={"prompt": "eval version body", "prompt_id": prompt_id, "version_id": version_id},
    ).json()

    by_run = app_client.post(
        "/api/evaluations/run",
        json={
            "run_id": tested["run_id"],
            "evaluator": {"rules": [{"type": "contains", "text": "Simulated answer"}]},
        },
    )
    assert by_run.status_code == 200, by_run.text
    assert by_run.json()["version_id"] == version_id

    by_execution = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {
                "prompt": "eval version body",
                "prompt_id": prompt_id,
                "version_id": version_id,
            },
            "evaluator": {"rules": [{"type": "contains", "text": "Simulated answer"}]},
        },
    )
    assert by_execution.status_code == 200, by_execution.text
    assert by_execution.json()["version_id"] == version_id


def test_comparison_carries_version_identity(app_client, db_ready):
    """Comparison sides expose version identity factually (no verdict change)."""
    _require_db(db_ready)
    created = _create_prompt(app_client, "compare version body")
    prompt_id = created["id"]
    version_id = _history(app_client, prompt_id)[0]["id"]
    left_id = app_client.post(
        "/api/testing/run",
        json={"prompt": "compare version body", "prompt_id": prompt_id, "version_id": version_id},
    ).json()["run_id"]
    right_id = app_client.post(
        "/api/testing/run",
        json={"prompt": "unversioned", "prompt_id": prompt_id},
    ).json()["run_id"]

    response = app_client.post(
        "/api/comparisons/run",
        json={
            "left_run_id": left_id,
            "right_run_id": right_id,
            "evaluator": {"rules": [{"type": "contains", "text": "Simulated answer"}]},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["left_execution"]["version_id"] == version_id
    assert body["right_execution"]["version_id"] is None
    for forbidden in ("winner", "better", "ranking", "recommendation"):
        assert forbidden not in response.text.lower()


# ------------------------------------------------------- database (33-35)


def test_schema_integrity(db_ready):
    """(33) version_id column exists; no new tables; PromptRun otherwise unchanged."""
    _require_db(db_ready)
    from sqlalchemy import inspect as sa_inspect

    from app.core.database import Base

    assert "version_id" in PromptRun.__table__.columns
    # 4B: alembic_version is migration bookkeeping, not an app table (same
    # exclusion as test_migrations.py); any other extra table still fails here.
    assert set(sa_inspect(engine).get_table_names()) - {"alembic_version"} == set(
        Base.metadata.tables.keys()
    )
    assert set(PromptRun.__table__.columns.keys()) == {
        "id", "prompt_id", "version_id", "provider", "model", "input_snapshot",
        "output_text", "status", "error", "finish_reason", "latency_ms",
        "usage_json", "created_at",
    }


def test_no_orphan_version_ids(db_ready):
    """(34) every non-NULL run version resolves to a real version row."""
    _require_db(db_ready)
    with SessionLocal() as session:
        orphans = session.execute(
            select(PromptRun.id)
            .outerjoin(PromptVersion, PromptVersion.id == PromptRun.version_id)
            .where(PromptRun.version_id.is_not(None))
            .where(PromptVersion.id.is_(None))
        ).all()
    assert orphans == []


def test_runs_are_never_mutated(app_client, db_ready):
    """(35) old runs keep version identity + output across later prompt activity."""
    _require_db(db_ready)
    created = _create_prompt(app_client, "immutable A")
    prompt_id = created["id"]
    v1 = _history(app_client, prompt_id)[0]["id"]
    run_id = app_client.post(
        "/api/testing/run",
        json={"prompt": "immutable A", "prompt_id": prompt_id, "version_id": v1},
    ).json()["run_id"]
    before = _run_row(run_id)

    app_client.put(f"/api/prompts/{prompt_id}", json={"body": "immutable B"})
    app_client.post(
        "/api/testing/run",
        json={"prompt": "immutable B", "prompt_id": prompt_id},
    )
    after = _run_row(run_id)

    assert (after.prompt_id, after.version_id, after.output_text, after.status) == (
        before.prompt_id, before.version_id, before.output_text, before.status,
    )
    assert after.version_id == uuid.UUID(v1)
