"""Phase 3F route tests for POST /api/comparisons/run.

Mirrors ``test_evaluation_routes.py`` conventions: the registry is swapped via the
same ``_REGISTRY_CACHE`` monkeypatch, DB-backed cases use the ``db_ready`` skip
pattern, and unexpected exceptions are asserted sanitized (no tracebacks, no
internal detail). Run-pair comparison never contacts a provider — MODE A reads
the two owned runs through the existing evaluation service — so the route works
even when no provider is configured.

Database cases (run-pair success, unknown/foreign 404, schema integrity) are
marked ``integration`` and skip cleanly when Postgres is down.
"""

import uuid

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import Base, SessionLocal, engine
from app.models import PromptRun

from conftest import FakeProvider

MAX_OUTPUT_DISPLAY = 10_000


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


def _registry_with(provider_cls, settings):
    from app.ai.registry import ProviderRegistry

    registry = ProviderRegistry(settings)
    registry.register(provider_cls(settings))
    return registry


def _use_registry(monkeypatch, ai_routes, registry):
    monkeypatch.setattr(ai_routes, "_REGISTRY_CACHE", registry)


def _evaluator(rules=None, name="qa check", expected_output=None) -> dict:
    rules = rules or [{"type": "contains", "text": "Simulated answer"}]
    payload = {"name": name, "rules": rules}
    if expected_output is not None:
        payload["expected_output"] = expected_output
    return payload


def _evaluation_payload(
    output="alpha",
    *,
    rules=None,
    passed=True,
    provider="fake",
    model="fake-model-1",
    latency_ms=12,
    usage=None,
    run_id=None,
    evidence=None,
) -> dict:
    rules = rules if rules is not None else [{"type": "contains", "text": "needle"}]
    evidence = (
        evidence
        if evidence is not None
        else {"matched": passed, "text": "needle"}
    )
    return {
        "run_id": run_id,
        "output": output,
        "provider": provider,
        "model": model,
        "latency_ms": latency_ms,
        "usage": usage,
        "evaluator_snapshot": {"name": "cfg", "rules": rules},
        "verdicts": [
            {
                "rule_id": "r1",
                "label": "rule",
                "type": rules[0]["type"],
                "passed": passed,
                "evidence": evidence,
            }
        ],
        "passed": passed,
    }


def _create_prompt(client, suffix: str) -> dict:
    response = client.post(
        "/api/prompts",
        json={
            "title": f"Comparison run {suffix}",
            "idea": "Test deterministic comparison of persisted PromptRuns.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_run(client, prompt_id: str, prompt_text: str) -> dict:
    response = client.post(
        "/api/testing/run",
        json={
            "prompt": prompt_text,
            "provider": "fake",
            "model": "fake-model-1",
            "prompt_id": prompt_id,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] is not None
    return body


def _create_foreign_run(db) -> PromptRun:
    """A PromptRun owned by a different user (ownership leak test)."""
    from app.models import Project, Prompt, User

    user = User(
        email=f"foreign-{uuid.uuid4().hex[:10]}@sparkprompt.local",
        display_name="Foreign workspace owner",
    )
    db.add(user)
    db.flush()
    project = Project(user_id=user.id, name="Foreign workspace")
    db.add(project)
    db.flush()
    prompt = Prompt(project_id=project.id, title="Foreign prompt", idea="hidden")
    db.add(prompt)
    db.flush()
    run = PromptRun(
        prompt_id=prompt.id,
        provider="fake",
        model="fake-model-1",
        input_snapshot={"messages": [{"role": "user", "content": "secret"}]},
        output_text="foreign output",
        status="success",
        finish_reason="stop",
        latency_ms=5,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


# --- supplied (MODE B) ---


def test_comparison_supplied_mode_success(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/comparisons/run",
        json={
            "left_evaluation": _evaluation_payload(output="Simulated answer one"),
            "right_evaluation": _evaluation_payload(output="Simulated answer two"),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["evaluator_compatibility"]["comparable"] is True
    assert body["evaluator_compatibility"]["status"] == "identical"
    assert len(body["criterion_diffs"]) == 1
    assert body["criterion_diffs"][0]["state"] == "same_pass"
    assert body["output_summary"]["identical"] is False
    assert body["output_summary"]["length_a"] == len("Simulated answer one")
    assert body["output_summary"]["length_b"] == len("Simulated answer two")
    assert body["prompt_diff"] is None  # supplied mode has no run snapshots
    assert body["metadata_diff"]["provider"]["changed"] is False
    assert body["metadata_diff"]["model"]["changed"] is False
    assert body["left_execution"]["run_id"] is None
    generated_at = body["generated_at"]
    assert generated_at


def test_comparison_supplied_mode_output_bounded(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    big = "x" * 15_000
    response = app_client.post(
        "/api/comparisons/run",
        json={
            "left_evaluation": _evaluation_payload(output=big),
            "right_evaluation": _evaluation_payload(output="small"),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["left_execution"]["output"]["truncated"] is True
    assert body["left_execution"]["output"]["length"] == 15_000
    assert len(body["left_execution"]["output"]["text"]) == MAX_OUTPUT_DISPLAY


def test_comparison_incompatible_supplied_evaluators_200(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/comparisons/run",
        json={
            "left_evaluation": _evaluation_payload(
                output="Simulated answer one",
                rules=[{"type": "contains", "text": "Python"}],
            ),
            "right_evaluation": _evaluation_payload(
                output="Simulated answer two",
                rules=[{"type": "contains", "text": "Java"}],
            ),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["evaluator_compatibility"]["comparable"] is False
    assert body["evaluator_compatibility"]["status"] == "incompatible"
    assert body["criterion_diffs"] == []
    mismatches = body["evaluator_compatibility"]["mismatches"]
    assert any(m["field"] == "text" and m["rule_index"] == 0 for m in mismatches)
    # Output comparison is still reported.
    assert body["output_summary"]["identical"] is False


def test_comparison_oversized_supplied_output_422(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    huge = "x" * 100_001
    response = app_client.post(
        "/api/comparisons/run",
        json={
            "left_evaluation": _evaluation_payload(output=huge),
            "right_evaluation": _evaluation_payload(output="small"),
        },
    )
    assert response.status_code == 422, response.text


# --- request validation (422) / domain errors (400) / gateway errors / 500 ---


def test_comparison_invalid_target_both_modes_422(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/comparisons/run",
        json={
            "left_run_id": str(uuid.uuid4()),
            "right_run_id": str(uuid.uuid4()),
            "evaluator": _evaluator(),
            "left_evaluation": _evaluation_payload(),
            "right_evaluation": _evaluation_payload(),
        },
    )
    assert response.status_code == 422, response.text


def test_comparison_invalid_target_neither_422(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post("/api/comparisons/run", json={})
    assert response.status_code == 422, response.text


def test_comparison_invalid_partial_run_pair_422(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    # Runs without the evaluator.
    response = app_client.post(
        "/api/comparisons/run",
        json={
            "left_run_id": str(uuid.uuid4()),
            "right_run_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 422, response.text
    # Evaluator alone is not enough.
    response = app_client.post(
        "/api/comparisons/run", json={"evaluator": _evaluator()}
    )
    assert response.status_code == 422, response.text
    # One supplied side only.
    response = app_client.post(
        "/api/comparisons/run",
        json={"left_evaluation": _evaluation_payload()},
    )
    assert response.status_code == 422, response.text


def test_comparison_domain_validation_error_400(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes
    from app.api.routes import comparison as comparison_routes
    from app.comparison import ComparisonValidationError

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))

    class ForcedService:
        def __init__(self, eval_service):
            del eval_service

        def compare(self, **kwargs):
            del kwargs
            raise ComparisonValidationError("forced validation failure")

    monkeypatch.setattr(comparison_routes, "ComparisonService", ForcedService)
    response = app_client.post(
        "/api/comparisons/run",
        json={
            "left_evaluation": _evaluation_payload(),
            "right_evaluation": _evaluation_payload(),
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "invalid_comparison_request"
    assert response.json()["detail"]["message"] == "forced validation failure"


def test_comparison_unexpected_error_sanitized_500(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes
    from app.api.routes import comparison as comparison_routes
    from app.comparison import ComparisonError

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))

    class ForcedService:
        def __init__(self, eval_service):
            del eval_service

        def compare(self, **kwargs):
            del kwargs
            raise ComparisonError("boom")

    monkeypatch.setattr(comparison_routes, "ComparisonService", ForcedService)
    response = app_client.post(
        "/api/comparisons/run",
        json={
            "left_evaluation": _evaluation_payload(),
            "right_evaluation": _evaluation_payload(),
        },
    )
    assert response.status_code == 500, response.text
    assert response.json()["detail"]["code"] == "comparison_failed"
    assert "boom" not in response.text
    assert "Traceback" not in response.text
    assert "File " not in response.text


def test_comparison_gateway_error_normalized(app_client, monkeypatch, settings):
    from app.ai.errors import AIProviderError, ErrorCode
    from app.api.routes import ai as ai_routes
    from app.api.routes import comparison as comparison_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))

    class ForcedService:
        def __init__(self, eval_service):
            del eval_service

        def compare(self, **kwargs):
            del kwargs
            raise AIProviderError("fake", ErrorCode.PROVIDER_ERROR, "upstream exploded")

    monkeypatch.setattr(comparison_routes, "ComparisonService", ForcedService)
    response = app_client.post(
        "/api/comparisons/run",
        json={
            "left_evaluation": _evaluation_payload(),
            "right_evaluation": _evaluation_payload(),
        },
    )
    assert response.status_code == 502, response.text
    assert response.json()["detail"]["code"] == "provider_error"
    assert "Traceback" not in response.text


# --- run-pair (MODE A) ---


@pytest.mark.integration
def test_comparison_run_pair_success(db_ready, monkeypatch, settings):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes
    from app.main import app

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    with TestClient(app) as client:
        prompt = _create_prompt(client, uuid.uuid4().hex[:8])
        left = _create_run(client, prompt["id"], "Write a welcome message")
        right = _create_run(client, prompt["id"], "Write a farewell message")

        # Snapshot the run rows before comparing (statelessness check).
        db = SessionLocal()
        try:
            before_left = db.get(PromptRun, uuid.UUID(left["run_id"]))
            before_right = db.get(PromptRun, uuid.UUID(right["run_id"]))
            before = {
                "left": (
                    before_left.output_text,
                    before_left.status,
                    before_left.provider,
                    before_left.model,
                    before_left.usage_json,
                    dict(before_left.input_snapshot),
                ),
                "right": (
                    before_right.output_text,
                    before_right.status,
                    before_right.provider,
                    before_right.model,
                    before_right.usage_json,
                    dict(before_right.input_snapshot),
                ),
            }
        finally:
            db.close()

        response = client.post(
            "/api/comparisons/run",
            json={
                "left_run_id": left["run_id"],
                "right_run_id": right["run_id"],
                "evaluator": _evaluator(
                    name="on-run pair check",
                    rules=[
                        {"type": "contains", "text": "Simulated answer"},
                        {"type": "min_length", "length": 10},
                    ],
                ),
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["left_execution"]["run_id"] == left["run_id"]
        assert body["right_execution"]["run_id"] == right["run_id"]
        assert body["left_execution"]["provider"] == "fake"
        assert body["right_execution"]["provider"] == "fake"
        assert body["evaluator_compatibility"]["comparable"] is True
        assert body["evaluator_compatibility"]["status"] == "identical"
        assert len(body["criterion_diffs"]) == 2
        assert body["criterion_diffs"][0]["state"] == "same_pass"
        # Different run prompts -> different executed prompts, different outputs.
        assert body["prompt_diff"] is not None
        assert body["prompt_diff"]["present"] is True
        assert body["prompt_diff"]["identical"] is False
        assert body["output_summary"]["identical"] is False
        assert body["output_summary"]["length_a"] == len(left["output"])
        assert body["output_summary"]["length_b"] == len(right["output"])
        assert body["metadata_diff"]["provider"]["changed"] is False
        assert body["metadata_diff"]["model"]["changed"] is False
        assert body["metadata_diff"]["usage"]["total_tokens"]["changed"] is False

        # The comparison is stateless: no rows changed, no rows added.
        db = SessionLocal()
        try:
            after_left = db.get(PromptRun, uuid.UUID(left["run_id"]))
            after_right = db.get(PromptRun, uuid.UUID(right["run_id"]))
            after = {
                "left": (
                    after_left.output_text,
                    after_left.status,
                    after_left.provider,
                    after_left.model,
                    after_left.usage_json,
                    dict(after_left.input_snapshot),
                ),
                "right": (
                    after_right.output_text,
                    after_right.status,
                    after_right.provider,
                    after_right.model,
                    after_right.usage_json,
                    dict(after_right.input_snapshot),
                ),
            }
            assert after == before
        finally:
            db.close()


@pytest.mark.integration
def test_comparison_unknown_run_404(db_ready, monkeypatch, settings):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes
    from app.main import app

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    with TestClient(app) as client:
        response = client.post(
            "/api/comparisons/run",
            json={
                "left_run_id": str(uuid.uuid4()),
                "right_run_id": str(uuid.uuid4()),
                "evaluator": _evaluator(),
            },
        )
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "Prompt run not found"


@pytest.mark.integration
def test_comparison_foreign_run_404(db_ready, monkeypatch, settings):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes
    from app.main import app

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    with TestClient(app) as client:
        db = SessionLocal()
        try:
            foreign = _create_foreign_run(db)
        finally:
            db.close()
        response = client.post(
            "/api/comparisons/run",
            json={
                "left_run_id": str(foreign.id),
                "right_run_id": str(foreign.id),
                "evaluator": _evaluator(),
            },
        )
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "Prompt run not found"


@pytest.mark.integration
def test_comparison_pair_with_foreign_run_404(db_ready, monkeypatch, settings):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes
    from app.main import app

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    with TestClient(app) as client:
        prompt = _create_prompt(client, uuid.uuid4().hex[:8])
        owned = _create_run(client, prompt["id"], "Write a welcome message")
        db = SessionLocal()
        try:
            foreign = _create_foreign_run(db)
        finally:
            db.close()
        for left_id, right_id in ((owned["run_id"], str(foreign.id)), (str(foreign.id), owned["run_id"])):
            response = client.post(
                "/api/comparisons/run",
                json={
                    "left_run_id": left_id,
                    "right_run_id": right_id,
                    "evaluator": _evaluator(),
                },
            )
            assert response.status_code == 404, response.text
            # Never reveal which side was foreign.
            assert response.json()["detail"] == "Prompt run not found"


# --- schema integrity: no comparison tables, PromptRun unchanged ---


def test_no_comparison_tables_in_metadata():
    """The comparison domain registers no tables on the SQLAlchemy metadata."""
    table_names = set(Base.metadata.tables.keys())
    for name in ("comparison_runs", "comparisons", "comparison_results", "comparison_criteria"):
        assert name not in table_names


@pytest.mark.integration
def test_no_comparison_tables_in_database(db_ready):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    table_names = set(inspect(engine).get_table_names())
    for name in ("comparison_runs", "comparisons", "comparison_results", "comparison_criteria"):
        assert name not in table_names


def test_promptrun_columns_unchanged_by_comparison():
    """PromptRun model gains nothing from Phase 3F; only 3O's version_id exists."""
    columns = set(PromptRun.__table__.columns.keys())
    assert columns == {
        "id",
        "prompt_id",
        "version_id",
        "provider",
        "model",
        "input_snapshot",
        "output_text",
        "status",
        "error",
        "finish_reason",
        "latency_ms",
        "usage_json",
        "created_at",
    }