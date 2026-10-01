"""Phase 3P Evaluation Persistence & History tests.

Covers the 3P matrix end to end: the append-only record model and API surface,
creation through the single canonical path (``POST /api/evaluations/run``),
snapshots/verdicts/score stored by value, bounded newest-first reads, ownership
(non-leaking 404s), immutability across version/restore/rule/weight changes,
versionless runs never inventing a version, sanitized failure envelopes,
cascade lifecycle, and reuse of a stored evaluation as a supplied comparison
result.

Conventions match the 3O suite: ``integration`` marker, module-scoped
``db_ready`` reachability skip, the conftest ``app_client`` (fake provider),
direct ``SessionLocal`` reads for stored-row claims, and a read-only check of
the migrated schema (``verify_schema_ready``) so no assertion depends on
test ordering.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal, engine
from app.evaluation import EvaluationError
from app.evaluation.types import EvaluationResult
from app.models import EvaluationRecord, Prompt, PromptRun, Project, User
from app.schemas.evaluation import EvaluationRecordRead

from conftest import ExplodingProvider

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


def _registry_with(provider_cls, settings):
    from app.ai.registry import ProviderRegistry

    registry = ProviderRegistry(settings)
    registry.register(provider_cls(settings))
    return registry


def _evaluator(rules=None, name="qa check", expected_output=None) -> dict:
    payload = {
        "name": name,
        "rules": rules or [{"type": "contains", "text": "welcome"}],
    }
    if expected_output is not None:
        payload["expected_output"] = expected_output
    return payload


def _create_prompt(client, body: str | None = None, **overrides) -> dict:
    payload = {
        "title": overrides.pop("title", f"3P history {uuid.uuid4().hex[:8]}"),
        "idea": overrides.pop("idea", "Keep what an evaluation said, for good."),
    }
    if body is not None:
        payload["body"] = body
    payload.update(overrides)
    response = client.post("/api/prompts", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _versions(client, prompt_id: str) -> list[dict]:
    response = client.get(f"/api/prompts/{prompt_id}/versions")
    assert response.status_code == 200, response.text
    return response.json()


def _make_run(
    client,
    prompt_id: str | None = None,
    version_id: str | None = None,
    prompt_text: str = "Write a welcome message",
) -> str:
    payload = {"prompt": prompt_text, "provider": "fake", "model": "fake-model-1"}
    if prompt_id is not None:
        payload["prompt_id"] = prompt_id
    if version_id is not None:
        payload["version_id"] = version_id
    response = client.post("/api/testing/run", json=payload)
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    assert run_id is not None
    return run_id


def _evaluate(
    client,
    *,
    run_id: str | None = None,
    execution: dict | None = None,
    rules=None,
    scoring: dict | None = None,
    name: str = "qa check",
):
    payload: dict = {"evaluator": _evaluator(rules=rules, name=name)}
    if run_id is not None:
        payload["run_id"] = run_id
    if execution is not None:
        payload["execution"] = execution
    if scoring is not None:
        payload["scoring"] = scoring
    return client.post("/api/evaluations/run", json=payload)


def _record_count() -> int:
    with SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(EvaluationRecord))


def _records_for_prompt(prompt_id: str) -> list[EvaluationRecord]:
    with SessionLocal() as session:
        rows = (
            session.scalars(
                select(EvaluationRecord)
                .where(EvaluationRecord.prompt_id == uuid.UUID(prompt_id))
                .order_by(EvaluationRecord.created_at)
            )
            .all()
        )
        for row in rows:
            session.expunge(row)
        return list(rows)


def _record(record_id: str) -> EvaluationRecord:
    with SessionLocal() as session:
        record = session.get(EvaluationRecord, uuid.UUID(record_id))
        assert record is not None, f"evaluation {record_id} was not persisted"
        session.expunge(record)
        return record


def _create_foreign_evaluation() -> tuple[str, str]:
    """Foreign workspace prompt + run + evaluation record. Returns (prompt_id, record_id)."""
    with SessionLocal() as session:
        user = User(
            email=f"foreign-3p-{uuid.uuid4().hex[:10]}@sparkprompt.local",
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
        run = PromptRun(
            prompt_id=prompt.id,
            provider="fake",
            model="fake-model-1",
            output_text="foreign secret output",
            status="success",
        )
        session.add(run)
        session.flush()
        record = EvaluationRecord(
            prompt_id=prompt.id,
            prompt_version_id=None,
            prompt_run_id=run.id,
            evaluator_snapshot={
                "name": "foreign check",
                "rules": [{"type": "contains", "text": "secret"}],
            },
            scoring_snapshot={"mode": "unweighted", "weights": []},
            verdicts=[
                {
                    "rule_id": None,
                    "label": None,
                    "type": "contains",
                    "passed": True,
                    "evidence": {"matched": True, "text": "foreign"},
                }
            ],
            passed=True,
            score=100.0,
        )
        session.add(record)
        session.flush()
        session.commit()
        return str(prompt.id), str(record.id)


# ------------------------------------------------------- model / API surface


def test_evaluation_record_model_shape():
    """Append-only record: anchor run NOT NULL, version nullable, no copied version."""
    table = EvaluationRecord.__table__
    columns = {column.name: column for column in table.columns}
    assert set(columns) == {
        "id",
        "prompt_id",
        "prompt_version_id",
        "prompt_run_id",
        "evaluator_snapshot",
        "scoring_snapshot",
        "verdicts",
        "passed",
        "score",
        "created_at",
    }
    # The run anchors the record; the version may legitimately be absent.
    assert columns["prompt_run_id"].nullable is False
    assert columns["prompt_id"].nullable is False
    assert columns["prompt_version_id"].nullable is True
    # Snapshots and verdicts are stored by value, never optional on read.
    for name in ("evaluator_snapshot", "scoring_snapshot", "verdicts", "passed", "created_at"):
        assert columns[name].nullable is False
    # The stored score may be null (no verdicts) but is never recomputed later.
    assert columns["score"].nullable is True
    # Version number is joined at read time from the immutable PromptVersion —
    # there is deliberately no second copy of version identity on the record.
    assert "version_number" not in columns

    foreign_keys = {
        foreign_key.column.table.name: foreign_key.ondelete
        for foreign_key in table.foreign_keys
    }
    assert foreign_keys == {
        "prompts": "CASCADE",
        "prompt_versions": "CASCADE",
        "prompt_runs": "CASCADE",
    }
    assert [index.name for index in table.indexes] == [
        "ix_evaluation_records_prompt_history"
    ]


def test_evaluation_result_evaluation_id_field_is_optional():
    """``evaluation_id`` is identity echo only: defaults to null, never the run id."""
    field = EvaluationResult.model_fields["evaluation_id"]
    assert field.default is None
    assert field.is_required() is False


def test_history_api_surface_exposes_no_update_or_delete():
    """Route introspection: one creation path, two read paths, nothing that rewrites."""
    from app.main import app

    routes = [
        (route.path, set(getattr(route, "methods", None) or []))
        for route in app.routes
        if "evaluation" in route.path
    ]
    assert any(
        path == "/api/evaluations/run" and "POST" in methods
        for path, methods in routes
    )
    assert any(
        path == "/api/evaluations/{evaluation_id}" and "GET" in methods
        for path, methods in routes
    )
    assert any(
        path == "/api/prompts/{prompt_id}/evaluations" and "GET" in methods
        for path, methods in routes
    )
    # Nothing on the evaluation surface can PUT/PATCH/DELETE a stored record,
    # and history creation is not a separate endpoint (POST /run owns creation).
    for path, methods in routes:
        assert not methods & {"PUT", "PATCH", "DELETE"}, (path, methods)
        if path.endswith("/evaluations"):
            assert methods == {"GET"}, (path, methods)


def test_history_write_attempts_answer_405():
    """The HTTP mirror of the surface test: rewrites are refused, not routed."""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    target = str(uuid.uuid4())
    for method in ("put", "patch", "delete"):
        response = getattr(client, method)(f"/api/evaluations/{target}")
        assert response.status_code == 405, (method, response.status_code)
    # Creation only ever happens through POST /api/evaluations/run.
    response = client.post(f"/api/prompts/{target}/evaluations", json={})
    assert response.status_code == 405, response.status_code


# ------------------------------------------------------------- creation (3P)


def test_run_evaluation_appends_one_record_and_echoes_its_id(app_client, db_ready):
    """Canonical creation path: one evaluation -> exactly one durable record."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    prompt = _create_prompt(app_client, body="welcome body")
    run_id = _make_run(
        app_client, prompt["id"], prompt_text="welcome body for the run"
    )
    before = _record_count()

    response = _evaluate(
        app_client,
        run_id=run_id,
        rules=[
            {"type": "contains", "text": "welcome"},
            {"type": "min_length", "length": 10},
        ],
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["evaluation_id"] is not None
    # The history id is its own identity — never the evaluated run.
    assert body["evaluation_id"] != body["run_id"]
    assert _record_count() == before + 1

    record = _record(body["evaluation_id"])
    assert record.prompt_id == uuid.UUID(prompt["id"])
    assert record.prompt_run_id == uuid.UUID(run_id)
    # Versionless run: no version is invented to fill the nullable column.
    assert record.prompt_version_id is None
    assert record.passed is body["passed"] is True
    assert record.score == body["score"] == 100.0
    # Snapshots and verdicts stored by value, identical to what was returned.
    assert [rule["type"] for rule in record.evaluator_snapshot["rules"]] == [
        "contains",
        "min_length",
    ]
    assert record.verdicts == body["verdicts"]
    assert record.scoring_snapshot["mode"] == "unweighted"
    assert record.created_at is not None

    # The evaluated run itself is untouched by persistence.
    with SessionLocal() as session:
        run = session.get(PromptRun, uuid.UUID(run_id))
        assert run is not None
        assert run.status == "success"
        assert run.version_id is None
        assert run.output_text == body["output"]


def test_draft_execution_evaluation_is_never_persisted(app_client, db_ready):
    """A run-less (draft) evaluation has nothing to anchor to: no record, null id."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    before = _record_count()

    response = _evaluate(
        app_client,
        execution={
            "prompt": "Write a welcome message",
            "provider": "fake",
            "model": "fake-model-1",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] is None
    assert body["evaluation_id"] is None
    assert _record_count() == before


def test_repeated_evaluations_of_one_run_are_all_appended(app_client, db_ready):
    """No deduplication on prompt_run_id: every evaluation is its own history row."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    prompt = _create_prompt(app_client, body="welcome body")
    run_id = _make_run(app_client, prompt["id"], prompt_text="welcome body here")
    before = _record_count()

    first = _evaluate(app_client, run_id=run_id, rules=[{"type": "contains", "text": "welcome"}])
    second = _evaluate(
        app_client,
        run_id=run_id,
        rules=[{"type": "contains", "text": "welcome"}, {"type": "min_length", "length": 5}],
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["evaluation_id"] != second.json()["evaluation_id"]
    assert _record_count() == before + 2

    rows = _records_for_prompt(prompt["id"])
    assert len(rows) == 2
    assert {row.id for row in rows} == {
        uuid.UUID(first.json()["evaluation_id"]),
        uuid.UUID(second.json()["evaluation_id"]),
    }
    assert {row.prompt_run_id for row in rows} == {uuid.UUID(run_id)}

    history = app_client.get(f"/api/prompts/{prompt['id']}/evaluations")
    assert history.status_code == 200, history.text
    assert len(history.json()) == 2


def test_suite_and_experiment_evaluations_are_not_persisted(app_client, db_ready):
    """Phase 3P stores individual evaluations only — suites and experiments save nothing."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    prompt = _create_prompt(app_client, body="welcome body")
    run_id = _make_run(app_client, prompt["id"], prompt_text="welcome body here")
    before = _record_count()

    suite = app_client.post(
        "/api/evaluations/suite",
        json={"evaluator": _evaluator(), "targets": [{"run_id": run_id}]},
    )
    assert suite.status_code == 200, suite.text
    assert suite.json()["total"] == 1
    for evaluation in suite.json()["evaluations"]:
        assert evaluation["evaluation_id"] is None

    experiment = app_client.post(
        "/api/experiments/run",
        json={"prompt_id": prompt["id"], "evaluator": _evaluator()},
    )
    assert experiment.status_code == 200, experiment.text
    assert experiment.json()["total_versions"] == 1
    for evaluation in experiment.json()["evaluations"]:
        assert evaluation["evaluation_id"] is None

    assert _record_count() == before
    # The suite/experiment run above did not create history for this prompt either.
    assert _records_for_prompt(prompt["id"]) == []


# ----------------------------------------------------------------- reads (3P)


def test_stored_detail_matches_the_evaluation_and_never_touches_a_provider(
    app_client, db_ready, monkeypatch, settings
):
    """GET detail is a pure read: identical payload every time, provider never invoked."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.api.routes import ai as ai_routes

    prompt = _create_prompt(app_client, body="welcome body")
    run_id = _make_run(app_client, prompt["id"], prompt_text="welcome body here")
    created = _evaluate(
        app_client,
        run_id=run_id,
        rules=[{"type": "contains", "text": "welcome"}, {"type": "min_length", "length": 10}],
    )
    assert created.status_code == 200, created.text
    body = created.json()
    evaluation_id = body["evaluation_id"]
    assert evaluation_id

    first = app_client.get(f"/api/evaluations/{evaluation_id}")
    assert first.status_code == 200, first.text
    detail = first.json()

    # The read reflects exactly what was stored at creation time.
    assert detail["evaluation_id"] == evaluation_id
    assert detail["prompt_id"] == prompt["id"]
    assert detail["prompt_run_id"] == run_id
    assert detail["prompt_version_id"] is None
    assert detail["version_number"] is None
    assert detail["output"] == body["output"]
    assert detail["provider"] == "fake"
    assert detail["model"] == "fake-model-1"
    assert detail["latency_ms"] == body["latency_ms"]
    assert detail["usage"] == body["usage"]
    assert detail["evaluator_snapshot"] == body["evaluator_snapshot"]
    assert detail["verdicts"] == body["verdicts"]
    assert detail["passed"] == body["passed"]
    assert detail["scoring"] == body["scoring"]
    assert detail["score"] == body["score"]
    assert detail["created_at"]

    # Any provider use would explode: the registry below can only fail loudly.
    monkeypatch.setattr(
        ai_routes, "_REGISTRY_CACHE", _registry_with(ExplodingProvider, settings)
    )
    second = app_client.get(f"/api/evaluations/{evaluation_id}")
    third = app_client.get(f"/api/evaluations/{evaluation_id}")
    assert second.status_code == 200, second.text
    assert second.json() == detail
    assert third.json() == detail

    # Re-evaluating under different current rules must not rewrite the record.
    rerun = _evaluate(
        app_client,
        run_id=run_id,
        rules=[{"type": "contains", "text": "nothing-matches-this"}],
    )
    assert rerun.status_code == 200, rerun.text
    assert rerun.json()["evaluation_id"] != evaluation_id
    after_rules = app_client.get(f"/api/evaluations/{evaluation_id}")
    assert after_rules.status_code == 200, after_rules.text
    assert after_rules.json() == detail


def test_unknown_and_foreign_evaluation_are_the_same_404(app_client, db_ready):
    """Ownership chain User -> Project -> Prompt -> record; no existence leak."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    _foreign_prompt_id, foreign_record_id = _create_foreign_evaluation()

    unknown = app_client.get(f"/api/evaluations/{uuid.uuid4()}")
    foreign = app_client.get(f"/api/evaluations/{foreign_record_id}")
    assert unknown.status_code == 404, unknown.text
    assert foreign.status_code == 404, foreign.text
    # Identical envelope: an unknown id never reveals that a foreign one exists.
    assert unknown.json() == foreign.json() == {"detail": "Evaluation not found"}
    for leak in ("foreign secret output", "Foreign prompt", "foreign workspace"):
        assert leak not in foreign.text


def test_history_list_unknown_and_foreign_prompt_are_the_same_404(
    app_client, db_ready
):
    """The list endpoint resolves ownership before reading a single row."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    foreign_prompt_id, _ = _create_foreign_evaluation()

    unknown = app_client.get(f"/api/prompts/{uuid.uuid4()}/evaluations")
    foreign = app_client.get(f"/api/prompts/{foreign_prompt_id}/evaluations")
    assert unknown.status_code == 404, unknown.text
    assert foreign.status_code == 404, foreign.text
    assert unknown.json() == foreign.json() == {"detail": "Prompt not found"}
    assert "foreign secret output" not in foreign.text


def test_history_list_is_newest_first_bounded_and_deterministic(
    app_client, db_ready
):
    """Bounded to the history cap, ordered created_at DESC with an id tie-break."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.services.evaluations import MAX_EVALUATION_HISTORY

    prompt = _create_prompt(app_client, body="welcome body")
    run_id = _make_run(app_client, prompt["id"], prompt_text="welcome body here")

    base = datetime.now(timezone.utc).replace(microsecond=0)
    inserted: list[tuple[datetime, uuid.UUID]] = []
    tie_ids: list[str] = []
    with SessionLocal() as session:
        for offset in range(52):
            record = EvaluationRecord(
                prompt_id=uuid.UUID(prompt["id"]),
                prompt_run_id=uuid.UUID(run_id),
                evaluator_snapshot={
                    "name": "history probe",
                    "rules": [{"type": "contains", "text": "welcome"}],
                },
                scoring_snapshot={"mode": "unweighted", "weights": []},
                verdicts=[
                    {
                        "rule_id": None,
                        "label": None,
                        "type": "contains",
                        "passed": True,
                        "evidence": {"matched": True, "text": "welcome"},
                    }
                ],
                passed=True,
                score=100.0,
                created_at=base - timedelta(minutes=offset),
            )
            session.add(record)
            session.flush()
            inserted.append((record.created_at, record.id))
        tie_time = base + timedelta(minutes=1)
        for _ in range(2):
            record = EvaluationRecord(
                prompt_id=uuid.UUID(prompt["id"]),
                prompt_run_id=uuid.UUID(run_id),
                evaluator_snapshot={
                    "name": "history probe",
                    "rules": [{"type": "contains", "text": "welcome"}],
                },
                scoring_snapshot={"mode": "unweighted", "weights": []},
                verdicts=[
                    {
                        "rule_id": None,
                        "label": None,
                        "type": "contains",
                        "passed": True,
                        "evidence": {"matched": True, "text": "welcome"},
                    }
                ],
                passed=True,
                score=100.0,
                created_at=tie_time,
            )
            session.add(record)
            session.flush()
            tie_ids.append(str(record.id))
        session.commit()

    response = app_client.get(f"/api/prompts/{prompt['id']}/evaluations")
    assert response.status_code == 200, response.text
    items = response.json()

    assert MAX_EVALUATION_HISTORY == 50
    assert len(items) == 50

    # Identical created_at rows are ordered by id descending (deterministic).
    assert [item["evaluation_id"] for item in items[:2]] == sorted(
        tie_ids, reverse=True
    )

    # Remaining rows are strictly newest first.
    times = [datetime.fromisoformat(item["created_at"]) for item in items]
    assert times == sorted(times, reverse=True)

    # The four oldest rows fall outside the bound and are never returned.
    oldest_ids = {str(record_id) for _, record_id in sorted(inserted)[:4]}
    returned_ids = {item["evaluation_id"] for item in items}
    assert oldest_ids.isdisjoint(returned_ids)

    # Rows carry location metadata only — no verdict evidence in a list.
    for item in items:
        assert "verdicts" not in item
        assert "evaluator_snapshot" not in item
        assert item["prompt_run_id"] == run_id


def test_versioned_evaluation_joins_its_version_number(app_client, db_ready):
    """The version number is read from the immutable PromptVersion, not copied."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    prompt = _create_prompt(app_client, body="welcome body v1")
    version = _versions(app_client, prompt["id"])[0]
    run_id = _make_run(
        app_client, prompt["id"], version_id=version["id"], prompt_text="welcome body v1"
    )

    created = _evaluate(app_client, run_id=run_id, rules=[{"type": "contains", "text": "welcome"}])
    assert created.status_code == 200, created.text
    evaluation_id = created.json()["evaluation_id"]

    record = _record(evaluation_id)
    assert record.prompt_version_id == uuid.UUID(version["id"])

    detail = app_client.get(f"/api/evaluations/{evaluation_id}").json()
    assert detail["prompt_version_id"] == version["id"]
    assert detail["version_number"] == version["version_number"]

    history = app_client.get(f"/api/prompts/{prompt['id']}/evaluations").json()
    assert history[0]["prompt_version_id"] == version["id"]
    assert history[0]["version_number"] == version["version_number"]
    assert history[0]["scoring_mode"] == "unweighted"


def test_versionless_evaluation_never_invents_a_version(app_client, db_ready):
    """A legacy (version_id NULL) run stays NULL everywhere: no guessed versions."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    prompt = _create_prompt(app_client, body="welcome body")
    # A version exists, but the run deliberately is not version-scoped.
    assert _versions(app_client, prompt["id"])
    run_id = _make_run(app_client, prompt["id"], prompt_text="welcome body here")

    created = _evaluate(app_client, run_id=run_id, rules=[{"type": "contains", "text": "welcome"}])
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["version_id"] is None

    record = _record(body["evaluation_id"])
    assert record.prompt_version_id is None

    detail = app_client.get(f"/api/evaluations/{body['evaluation_id']}").json()
    assert detail["prompt_version_id"] is None
    assert detail["version_number"] is None

    history = app_client.get(f"/api/prompts/{prompt['id']}/evaluations").json()
    assert history[0]["prompt_version_id"] is None
    assert history[0]["version_number"] is None


# ----------------------------------------------------------- immutability (3P)


def test_stored_evaluation_survives_version_restore_and_evaluator_changes(
    app_client, db_ready
):
    """Snapshots are by value: later versions, restores, and rules change nothing."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    prompt = _create_prompt(app_client, body="welcome body A")
    first_version = _versions(app_client, prompt["id"])[0]
    run_id = _make_run(
        app_client,
        prompt["id"],
        version_id=first_version["id"],
        prompt_text="welcome body A",
    )

    created = _evaluate(
        app_client,
        run_id=run_id,
        rules=[{"type": "contains", "text": "welcome"}],
    )
    assert created.status_code == 200, created.text
    evaluation_id = created.json()["evaluation_id"]

    detail_before = app_client.get(f"/api/evaluations/{evaluation_id}")
    assert detail_before.status_code == 200, detail_before.text
    before = detail_before.json()
    assert before["output"] == "Simulated answer to: welcome body A"
    assert before["version_number"] == first_version["version_number"]

    # Change everything mutable around the record: prompt body (new versions),
    # a restore of the original version, the rule set, and the scoring profile.
    for body in ("welcome body B", "entirely different body C"):
        updated = app_client.put(f"/api/prompts/{prompt['id']}", json={"body": body})
        assert updated.status_code == 200, updated.text
    restored = app_client.post(
        f"/api/prompts/{prompt['id']}/versions/{first_version['id']}/restore"
    )
    assert restored.status_code == 201, restored.text

    other_run = _make_run(
        app_client, prompt["id"], prompt_text="entirely different body C"
    )
    newer = _evaluate(
        app_client,
        run_id=other_run,
        rules=[{"id": "r1", "type": "contains", "text": "different"}],
        scoring={"mode": "weighted", "weights": [{"rule_id": "r1", "weight": 4}]},
    )
    assert newer.status_code == 200, newer.text
    assert newer.json()["evaluation_id"] != evaluation_id

    detail_after = app_client.get(f"/api/evaluations/{evaluation_id}")
    assert detail_after.status_code == 200, detail_after.text
    assert detail_after.json() == before

    # The list row for the original evaluation is equally unchanged, and the
    # newest row describes only its own evaluation.
    history = app_client.get(f"/api/prompts/{prompt['id']}/evaluations").json()
    assert len(history) == 2
    original_row = next(row for row in history if row["evaluation_id"] == evaluation_id)
    assert original_row["score"] == before["score"]
    assert original_row["scoring_mode"] == "unweighted"
    assert original_row["version_number"] == first_version["version_number"]
    newest_row = next(
        row for row in history if row["evaluation_id"] != evaluation_id
    )
    assert newest_row["scoring_mode"] == "weighted"
    assert newest_row["score"] == 100.0


def test_weighted_evaluation_stores_exact_weights_and_score(app_client, db_ready):
    """Weighted weights and the derived score are stored verbatim, then stay put."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    prompt = _create_prompt(app_client, body="welcome body")
    run_id = _make_run(app_client, prompt["id"], prompt_text="welcome body here")
    weights = [
        {"rule_id": "r1", "weight": 3},
        {"rule_id": "r2", "weight": 1},
    ]
    created = _evaluate(
        app_client,
        run_id=run_id,
        rules=[
            {"id": "r1", "type": "contains", "text": "welcome"},  # passes
            {"id": "r2", "type": "contains", "text": "never-present"},  # fails
        ],
        scoring={"mode": "weighted", "weights": weights},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    # 3 of 4 weighted units pass -> 75.0, not the unweighted 50.0.
    assert body["score"] == 75.0

    record = _record(body["evaluation_id"])
    assert record.score == 75.0
    assert record.scoring_snapshot["mode"] == "weighted"
    stored_weights = {
        entry["rule_id"]: entry["weight"] for entry in record.scoring_snapshot["weights"]
    }
    assert stored_weights == {"r1": 3.0, "r2": 1.0}

    detail = app_client.get(f"/api/evaluations/{body['evaluation_id']}").json()
    assert detail["scoring"]["mode"] == "weighted"
    assert {
        entry["rule_id"]: entry["weight"] for entry in detail["scoring"]["weights"]
    } == {"r1": 3.0, "r2": 1.0}
    assert detail["score"] == 75.0

    history = app_client.get(f"/api/prompts/{prompt['id']}/evaluations").json()
    assert history[0]["scoring_mode"] == "weighted"
    assert history[0]["score"] == 75.0

    # A later unweighted evaluation never rewrites the stored weighted one.
    unweighted = _evaluate(
        app_client,
        run_id=run_id,
        rules=[
            {"id": "r1", "type": "contains", "text": "welcome"},
            {"id": "r2", "type": "contains", "text": "never-present"},
        ],
    )
    assert unweighted.status_code == 200, unweighted.text
    assert unweighted.json()["score"] == 50.0
    unchanged = app_client.get(f"/api/evaluations/{body['evaluation_id']}").json()
    assert unchanged["score"] == 75.0
    assert unchanged["scoring"]["mode"] == "weighted"


# ------------------------------------------------------- failure paths (3P)


def test_persistence_failure_is_rolled_back_and_sanitized(
    app_client, db_ready, monkeypatch
):
    """A failed write is never reported as a saved evaluation: 500, no leaks, no row."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.services import evaluations as evaluations_service

    prompt = _create_prompt(app_client, body="welcome body")
    run_id = _make_run(app_client, prompt["id"], prompt_text="welcome body here")
    before = _record_count()

    def _explode(db, result):
        raise SQLAlchemyError('psql: FATAL: password authentication failed for "sparkprompt"')

    monkeypatch.setattr(evaluations_service, "create_evaluation_record", _explode)
    failed = _evaluate(app_client, run_id=run_id)
    assert failed.status_code == 500, failed.text
    detail = failed.json()["detail"]
    assert detail["code"] == "evaluation_persistence_failed"
    assert detail["message"] == "The evaluation could not be saved."
    for leak in ("psql", "password", "authentication", "Traceback", "sqlalchemy"):
        assert leak not in failed.text
    assert _record_count() == before

    def _identity_mismatch(db, result):
        raise EvaluationError("Evaluation identity does not match the stored run.")

    monkeypatch.setattr(
        evaluations_service, "create_evaluation_record", _identity_mismatch
    )
    refused = _evaluate(app_client, run_id=run_id)
    assert refused.status_code == 500, refused.text
    assert refused.json()["detail"]["code"] == "evaluation_persistence_failed"
    assert "identity does not match" not in refused.text
    assert _record_count() == before


def test_corrupted_snapshot_fails_loudly_on_read(app_client, db_ready):
    """Tampered stored JSON is a sanitized 500 — never a partial or repaired history."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    prompt = _create_prompt(app_client, body="welcome body")
    run_id = _make_run(app_client, prompt["id"], prompt_text="welcome body here")
    created = _evaluate(app_client, run_id=run_id)
    assert created.status_code == 200, created.text
    evaluation_id = created.json()["evaluation_id"]

    with SessionLocal() as session:
        record = session.get(EvaluationRecord, uuid.UUID(evaluation_id))
        assert record is not None
        record.scoring_snapshot = {"mode": "not-a-real-mode", "weights": []}
        session.commit()

    detail = app_client.get(f"/api/evaluations/{evaluation_id}")
    assert detail.status_code == 500, detail.text
    assert detail.json()["detail"] == {
        "status": "error",
        "code": "evaluation_failed",
        "message": "Stored evaluation could not be read.",
    }
    for leak in ("not-a-real-mode", "Traceback", "ValidationError"):
        assert leak not in detail.text

    history = app_client.get(f"/api/prompts/{prompt['id']}/evaluations")
    assert history.status_code == 500, history.text
    assert history.json()["detail"] == {
        "status": "error",
        "code": "evaluation_failed",
        "message": "Evaluation history could not be read.",
    }
    for leak in ("not-a-real-mode", "Traceback", "ValidationError"):
        assert leak not in history.text


# ------------------------------------------------------------- lifecycle (3P)


def test_deleting_a_prompt_cascades_its_evaluation_history(app_client, db_ready):
    """Records die with their prompt, exactly like the rest of the project's tables."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    prompt = _create_prompt(app_client, body="welcome body")
    run_id = _make_run(app_client, prompt["id"], prompt_text="welcome body here")
    created = _evaluate(app_client, run_id=run_id)
    assert created.status_code == 200, created.text
    assert len(_records_for_prompt(prompt["id"])) == 1

    deleted = app_client.delete(f"/api/prompts/{prompt['id']}")
    assert deleted.status_code == 200, deleted.text
    assert _records_for_prompt(prompt["id"]) == []
    with SessionLocal() as session:
        assert session.get(EvaluationRecord, uuid.UUID(created.json()["evaluation_id"])) is None


# --------------------------------------------------------- compatibility (3P)


def test_stored_evaluation_reuses_as_a_supplied_comparison(app_client, db_ready):
    """``to_evaluation_result`` feeds Phase 3F unchanged: same ids, score, verdicts."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.comparison import ComparisonService
    from app.evaluation import EvaluationService

    prompt = _create_prompt(app_client, body="welcome body")
    passing_run = _make_run(app_client, prompt["id"], prompt_text="welcome message")
    failing_run = _make_run(app_client, prompt["id"], prompt_text="quiet message")

    rules = [
        {"type": "contains", "text": "welcome"},
        {"type": "min_length", "length": 5},
    ]
    left_id = _evaluate(app_client, run_id=passing_run, rules=rules).json()["evaluation_id"]
    right_id = _evaluate(app_client, run_id=failing_run, rules=rules).json()["evaluation_id"]
    assert left_id and right_id and left_id != right_id

    left_payload = app_client.get(f"/api/evaluations/{left_id}").json()
    right_payload = app_client.get(f"/api/evaluations/{right_id}").json()

    left = EvaluationRecordRead.model_validate(left_payload).to_evaluation_result()
    right = EvaluationRecordRead.model_validate(right_payload).to_evaluation_result()

    # Identity maps onto the Phase 3E wire names; the score is the stored one.
    assert str(left.run_id) == left_payload["prompt_run_id"] == passing_run
    assert left.version_id is None
    assert left.evaluation_id is None
    assert left.score == left_payload["score"] == 100.0
    assert right.score == right_payload["score"] == 50.0
    assert left.output == left_payload["output"]
    assert [verdict.model_dump(mode="json") for verdict in left.verdicts] == (
        left_payload["verdicts"]
    )
    assert left.evaluator_snapshot.model_dump(mode="json") == left_payload["evaluator_snapshot"]

    # The stored results are usable by the frozen comparison service as-is.
    service = ComparisonService(EvaluationService(object()))
    comparison = service.compare(left_evaluation=left, right_evaluation=right)
    assert comparison.evaluator_compatibility.comparable is True
    assert len(comparison.criterion_diffs) == 2
    assert comparison.output_summary.identical is False
    assert str(comparison.left_execution.run_id) == passing_run
    assert str(comparison.right_execution.run_id) == failing_run
