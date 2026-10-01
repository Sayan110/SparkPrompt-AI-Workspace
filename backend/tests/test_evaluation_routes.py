"""Phase 3E route tests for POST /api/evaluations/run.

Mirrors ``test_testing_routes.py`` conventions: the registry is swapped via the same
``_REGISTRY_CACHE`` monkeypatch, DB-backed cases use the ``db_ready`` skip pattern, and
unexpected exceptions are asserted sanitized (no tracebacks, no internal detail).

Database cases (MODE A, unknown-run 404, schema integrity) are marked ``integration``
and skip cleanly when Postgres is down.
"""

import uuid

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import Base, SessionLocal, engine
from app.models import PromptRun

from conftest import FailingProvider, ExplodingProvider, FakeProvider


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


def _create_prompt(client, suffix: str) -> dict:
    response = client.post(
        "/api/prompts",
        json={
            "title": f"Evaluation run {suffix}",
            "idea": "Test deterministic evaluation of a persisted PromptRun.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- MODE B: fresh execution ---


def test_evaluate_run_fresh_execution_success(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    payload = {
        "execution": {"prompt": "Write a welcome message", "provider": "fake", "model": "fake-model-1"},
        "evaluator": _evaluator(),
    }
    response = app_client.post("/api/evaluations/run", json=payload)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["output"] == "Simulated answer to: Write a welcome message"
    assert body["provider"] == "fake"
    assert body["model"] == "fake-model-1"
    assert body["latency_ms"] is not None
    assert body["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 25,
        "total_tokens": 35,
    }
    assert body["run_id"] is None  # no prompt_id attached -> nothing persisted
    assert body["passed"] is True
    assert len(body["verdicts"]) == 1
    assert body["verdicts"][0]["type"] == "contains"
    assert body["verdicts"][0]["passed"] is True
    assert body["verdicts"][0]["evidence"] == {
        "matched": True,
        "text": "Simulated answer",
    }
    # Evaluator exactly as executed.
    assert body["evaluator_snapshot"]["name"] == "qa check"
    assert body["evaluator_snapshot"]["rules"][0]["text"] == "Simulated answer"
    assert body["evaluator_snapshot"]["rules"][0]["case_sensitive"] is True
    assert body["generated_at"]


def test_evaluate_run_fresh_execution_failure_verdicts(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    payload = {
        "execution": {"prompt": "Write a welcome message", "provider": "fake", "model": "fake-model-1"},
        "evaluator": {
            "rules": [
                {"type": "contains", "text": "Java"},
                {"type": "min_length", "length": 10, "strip": True},
            ]
        },
    }
    response = app_client.post("/api/evaluations/run", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["passed"] is False
    assert [v["passed"] for v in body["verdicts"]] == [False, True]
    assert body["verdicts"][1]["type"] == "min_length"
    assert body["verdicts"][1]["evidence"] == {
        "actual_length": len("Simulated answer to: Write a welcome message"),
        "minimum": 10,
    }


def test_evaluate_run_exact_match_uses_expected_output(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    expected = "Simulated answer to: Write a welcome message"
    payload = {
        "execution": {"prompt": "Write a welcome message", "provider": "fake", "model": "fake-model-1"},
        "evaluator": _evaluator(
            rules=[{"type": "exact_match"}],
            expected_output=expected,
        ),
    }
    response = app_client.post("/api/evaluations/run", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["passed"] is True
    assert body["verdicts"][0]["passed"] is True
    # Bounded evidence, never the output itself.
    assert body["verdicts"][0]["evidence"] == {
        "matched": True,
        "expected_length": len(expected),
        "actual_length": len(expected),
    }


# --- validation (422) / domain errors (400) / gateway errors ---


def test_evaluate_run_missing_evaluator_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/evaluations/run", json={"execution": {"prompt": "Write a haiku"}}
    )
    assert response.status_code == 422, response.text


def test_evaluate_run_invalid_target_neither_supplied(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/evaluations/run", json={"evaluator": _evaluator()}
    )
    assert response.status_code == 422, response.text


def test_evaluate_run_invalid_target_both_supplied(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/evaluations/run",
        json={
            "run_id": str(uuid.uuid4()),
            "execution": {"prompt": "Write a haiku"},
            "evaluator": _evaluator(),
        },
    )
    assert response.status_code == 422, response.text


def test_evaluate_run_unknown_rule_type_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {"prompt": "Write a haiku"},
            "evaluator": _evaluator(rules=[{"type": "semantic_judge"}]),
        },
    )
    assert response.status_code == 422, response.text


def test_evaluate_run_invalid_regex_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {"prompt": "Write a haiku"},
            "evaluator": _evaluator(rules=[{"type": "regex_match", "pattern": "["}]),
        },
    )
    assert response.status_code == 422, response.text


def test_evaluate_run_rule_limit_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    rules = [{"type": "contains", "text": f"needle {i}"} for i in range(21)]
    response = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {"prompt": "Write a haiku"},
            "evaluator": _evaluator(rules=rules),
        },
    )
    assert response.status_code == 422, response.text


def test_evaluate_run_blank_rule_text_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {"prompt": "Write a haiku"},
            "evaluator": _evaluator(rules=[{"type": "contains", "text": "   "}]),
        },
    )
    assert response.status_code == 422, response.text


def test_evaluate_run_gateway_error_normalized(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FailingProvider, settings))
    response = app_client.post(
        "/api/evaluations/run",
        json={"execution": {"prompt": "Write a haiku"}, "evaluator": _evaluator()},
    )
    assert response.status_code == 502, response.text
    assert response.json()["detail"]["code"] == "provider_error"
    # Normalized message only — never a raw stack trace or upstream body.
    assert "Traceback" not in response.text
    assert "File " not in response.text


def test_evaluate_run_unexpected_error_sanitized(monkeypatch, settings):
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes
    from app.main import app

    _use_registry(monkeypatch, ai_routes, _registry_with(ExplodingProvider, settings))
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/evaluations/run",
        json={"execution": {"prompt": "Write a haiku"}, "evaluator": _evaluator()},
    )
    assert response.status_code == 500, response.text
    assert "unexpected-internal-detail" not in response.text
    assert "RuntimeError" not in response.text


def test_evaluate_run_domain_validation_error_sanitized(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes
    from app.api.routes import evaluation as evaluation_routes
    from app.evaluation import EvaluationValidationError

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))

    class ForcedService:
        def __init__(self, test_service):
            del test_service

        def evaluate(self, **kwargs):
            del kwargs
            raise EvaluationValidationError("forced validation failure")

    monkeypatch.setattr(evaluation_routes, "EvaluationService", ForcedService)
    response = app_client.post(
        "/api/evaluations/run",
        json={"execution": {"prompt": "Write a haiku"}, "evaluator": _evaluator()},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "invalid_evaluation_request"
    assert response.json()["detail"]["message"] == "forced validation failure"


def test_evaluate_run_domain_error_sanitized_500(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes
    from app.api.routes import evaluation as evaluation_routes
    from app.evaluation import EvaluationError

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))

    class ForcedService:
        def __init__(self, test_service):
            del test_service

        def evaluate(self, **kwargs):
            del kwargs
            raise EvaluationError("boom")

    monkeypatch.setattr(evaluation_routes, "EvaluationService", ForcedService)
    response = app_client.post(
        "/api/evaluations/run",
        json={"execution": {"prompt": "Write a haiku"}, "evaluator": _evaluator()},
    )
    assert response.status_code == 500, response.text
    assert response.json()["detail"]["code"] == "evaluation_failed"
    assert "boom" not in response.text


# --- MODE A: existing PromptRun ---


@pytest.mark.integration
def test_evaluate_run_existing_run(db_ready, monkeypatch, settings):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes
    from app.main import app

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    with TestClient(app) as client:
        prompt = _create_prompt(client, uuid.uuid4().hex[:8])
        test_run = client.post(
            "/api/testing/run",
            json={
                "prompt": "Write a welcome message",
                "provider": "fake",
                "model": "fake-model-1",
                "prompt_id": prompt["id"],
            },
        )
        assert test_run.status_code == 200, test_run.text
        run_id = test_run.json()["run_id"]
        assert run_id is not None

        response = client.post(
            "/api/evaluations/run",
            json={
                "run_id": run_id,
                "evaluator": _evaluator(
                    name="on-run check",
                    rules=[
                        {"type": "contains", "text": "welcome"},
                        {"type": "min_length", "length": 10},
                    ],
                ),
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["run_id"] == run_id
        assert body["output"] == test_run.json()["output"]
        assert body["provider"] == "fake"
        assert body["model"] == "fake-model-1"
        assert body["latency_ms"] is not None
        assert body["usage"]["total_tokens"] == 35
        assert body["passed"] is True
        assert len(body["verdicts"]) == 2
        assert body["verdicts"][0]["type"] == "contains"
        assert body["verdicts"][0]["passed"] is True

        # The evaluated run row is untouched (read-only MODE A).
        db = SessionLocal()
        try:
            run = db.get(PromptRun, uuid.UUID(run_id))
            assert run is not None
            assert run.output_text == test_run.json()["output"]
            assert run.status == "success"
            assert run.provider == "fake"
            assert run.model == "fake-model-1"
            assert run.usage_json["total_tokens"] == 35
        finally:
            db.close()


@pytest.mark.integration
def test_evaluate_run_existing_run_failure(db_ready, monkeypatch, settings):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes
    from app.main import app

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    with TestClient(app) as client:
        prompt = _create_prompt(client, uuid.uuid4().hex[:8])
        test_run = client.post(
            "/api/testing/run",
            json={
                "prompt": "Write a welcome message",
                "provider": "fake",
                "model": "fake-model-1",
                "prompt_id": prompt["id"],
            },
        )
        run_id = test_run.json()["run_id"]

        response = client.post(
            "/api/evaluations/run",
            json={
                "run_id": run_id,
                "evaluator": _evaluator(rules=[{"type": "contains", "text": "Java"}]),
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["passed"] is False
        assert body["verdicts"][0]["passed"] is False


@pytest.mark.integration
def test_evaluate_run_unknown_run_404(db_ready, monkeypatch, settings):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes
    from app.main import app

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    with TestClient(app) as client:
        response = client.post(
            "/api/evaluations/run",
            json={
                "run_id": str(uuid.uuid4()),
                "evaluator": _evaluator(rules=[{"type": "contains", "text": "x"}]),
            },
        )
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "Prompt run not found"


# --- schema integrity: no evaluation tables, PromptRun unchanged ---


def test_no_evaluation_tables_in_metadata():
    """The evaluation domain registers no tables on the SQLAlchemy metadata."""
    table_names = set(Base.metadata.tables.keys())
    for name in ("evaluation_runs", "evaluations", "evaluation_criteria", "evaluation_results"):
        assert name not in table_names


@pytest.mark.integration
def test_no_evaluation_tables_in_database(db_ready):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    table_names = set(inspect(engine).get_table_names())
    for name in ("evaluation_runs", "evaluations", "evaluation_criteria", "evaluation_results"):
        assert name not in table_names


def test_promptrun_columns_unchanged():
    """PromptRun model is untouched by Phase 3E (only 3O's version_id exists)."""
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


@pytest.mark.integration
def test_promptrun_table_unchanged_in_database(db_ready):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    columns = {col["name"] for col in inspect(engine).get_columns("prompt_runs")}
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