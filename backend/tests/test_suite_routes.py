"""Phase 3G suite route tests for POST /api/evaluations/suite.

Mirrors ``test_evaluation_routes.py`` conventions: the registry is swapped via the same
``_REGISTRY_CACHE`` monkeypatch, DB-backed cases use the ``db_ready`` skip pattern, and
unexpected exceptions are asserted sanitized (no tracebacks, no internal detail).

Database cases (MODE A targets, unknown-run 404s, schema integrity) are marked
``integration`` and skip cleanly when Postgres is down.
"""

import uuid

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import Base, SessionLocal, engine
from app.models import PromptRun

from conftest import (
    ExplodingProvider,
    FailingProvider,
    FakeProvider,
    UnavailableProvider,
)


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


def _execution(prompt: str) -> dict:
    return {"prompt": prompt, "provider": "fake", "model": "fake-model-1"}


def _suite(targets: list[dict], rules=None) -> dict:
    return {"evaluator": _evaluator(rules=rules), "targets": targets}


# --- MODE B: fresh executions ---


def test_suite_fresh_executions_success(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/evaluations/suite",
        json=_suite(
            [
                {"execution": _execution("Write a welcome message")},
                {"execution": _execution("Write a thank-you note")},
                {"execution": _execution("Write a farewell")},
            ]
        ),
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total"] == 3
    assert body["passed"] == 3
    assert len(body["evaluations"]) == 3
    # Order preserved.
    assert body["evaluations"][0]["output"] == "Simulated answer to: Write a welcome message"
    assert body["evaluations"][1]["output"] == "Simulated answer to: Write a thank-you note"
    assert body["evaluations"][2]["output"] == "Simulated answer to: Write a farewell"
    for evaluation in body["evaluations"]:
        assert evaluation["provider"] == "fake"
        assert evaluation["model"] == "fake-model-1"
        assert evaluation["usage"] == {
            "prompt_tokens": 10,
            "completion_tokens": 25,
            "total_tokens": 35,
        }
        assert evaluation["run_id"] is None  # no prompt_id -> nothing persisted
        assert evaluation["passed"] is True
        assert evaluation["verdicts"][0]["type"] == "contains"
        assert evaluation["evaluator_snapshot"]["name"] == "qa check"
    assert body["generated_at"]


def test_suite_fresh_executions_mixed_counts(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/evaluations/suite",
        json=_suite(
            [
                {"execution": _execution("Write a welcome message")},
                {"execution": _execution("Write a haiku")},
                {"execution": _execution("Write a limerick")},
            ],
            rules=[{"type": "contains", "text": "welcome"}],
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    assert body["passed"] == 1
    assert [e["passed"] for e in body["evaluations"]] == [True, False, False]


def test_suite_fresh_executions_all_fail(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/evaluations/suite",
        json=_suite(
            [
                {"execution": _execution("Write a welcome message")},
                {"execution": _execution("Write a haiku")},
            ],
            rules=[{"type": "contains", "text": "no-such-needle"}],
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert body["passed"] == 0


def test_suite_single_target_parity_with_run(app_client, monkeypatch, settings):
    """A one-target suite over a fresh execution == the Phase 3E /run result."""
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    run_response = app_client.post(
        "/api/evaluations/run",
        json={"execution": _execution("Write a welcome message"), "evaluator": _evaluator()},
    )
    assert run_response.status_code == 200, run_response.text

    suite_response = app_client.post(
        "/api/evaluations/suite",
        json=_suite([{"execution": _execution("Write a welcome message")}]),
    )
    assert suite_response.status_code == 200, suite_response.text
    wrapper = suite_response.json()
    assert wrapper["total"] == 1
    assert wrapper["passed"] == 1

    run = run_response.json()
    [evaluation] = wrapper["evaluations"]
    for key in ("output", "provider", "model", "passed", "verdicts", "usage"):
        assert evaluation[key] == run[key], key


# --- 422 validation (wire layer) ---


def test_suite_empty_targets_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/evaluations/suite", json=_suite([])
    )
    assert response.status_code == 422, response.text


def test_suite_too_many_targets_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/evaluations/suite",
        json=_suite(
            [{"execution": _execution(f"prompt {i}")} for i in range(21)]
        ),
    )
    assert response.status_code == 422, response.text


def test_suite_duplicate_run_id_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    run_id = str(uuid.uuid4())
    response = app_client.post(
        "/api/evaluations/suite",
        json=_suite([{"run_id": run_id}, {"run_id": run_id}]),
    )
    assert response.status_code == 422, response.text


def test_suite_duplicate_execution_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    execution = _execution("Write a welcome message")
    response = app_client.post(
        "/api/evaluations/suite",
        json=_suite([{"execution": execution}, {"execution": execution}]),
    )
    assert response.status_code == 422, response.text


def test_suite_target_neither_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post("/api/evaluations/suite", json=_suite([{}]))
    assert response.status_code == 422, response.text


def test_suite_target_both_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/evaluations/suite",
        json=_suite(
            [{"run_id": str(uuid.uuid4()), "execution": _execution("Write a welcome message")}]
        ),
    )
    assert response.status_code == 422, response.text


def test_suite_missing_evaluator_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/evaluations/suite",
        json={"targets": [{"execution": _execution("Write a welcome message")}]},
    )
    assert response.status_code == 422, response.text


def test_suite_invalid_rule_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/evaluations/suite",
        json=_suite(
            [{"execution": _execution("Write a welcome message")}],
            rules=[{"type": "semantic_judge"}],
        ),
    )
    assert response.status_code == 422, response.text


def test_suite_blank_execution_prompt_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/evaluations/suite",
        json=_suite([{"execution": {"prompt": "   "}}]),
    )
    assert response.status_code == 422, response.text


# --- gateway/domain error envelope ---


def test_suite_gateway_error_normalized(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FailingProvider, settings))
    response = app_client.post(
        "/api/evaluations/suite",
        json=_suite([{"execution": _execution("Write a welcome message")}]),
    )
    assert response.status_code == 502, response.text
    assert response.json()["detail"]["code"] == "provider_error"
    assert "Traceback" not in response.text


def test_suite_provider_unavailable_409(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(UnavailableProvider, settings))
    response = app_client.post(
        "/api/evaluations/suite",
        json=_suite([{"execution": _execution("Write a welcome message")}]),
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "provider_unavailable"


def test_suite_unexpected_provider_error_sanitized(monkeypatch, settings):
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes
    from app.main import app

    _use_registry(monkeypatch, ai_routes, _registry_with(ExplodingProvider, settings))
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/evaluations/suite",
        json=_suite([{"execution": _execution("Write a welcome message")}]),
    )
    assert response.status_code == 500, response.text
    assert "unexpected-internal-detail" not in response.text
    assert "RuntimeError" not in response.text


def test_suite_domain_validation_error_sanitized(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes
    from app.api.routes import evaluation as evaluation_routes
    from app.evaluation import EvaluationValidationError

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))

    class ForcedSuite:
        def __init__(self, eval_service):
            del eval_service

        def run_suite(self, **kwargs):
            del kwargs
            raise EvaluationValidationError("forced suite validation failure")

    monkeypatch.setattr(evaluation_routes, "EvaluationSuiteService", ForcedSuite)
    response = app_client.post(
        "/api/evaluations/suite",
        json=_suite([{"execution": _execution("Write a welcome message")}]),
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "invalid_evaluation_request"
    assert response.json()["detail"]["message"] == "forced suite validation failure"


def test_suite_domain_error_sanitized_500(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes
    from app.api.routes import evaluation as evaluation_routes
    from app.evaluation import EvaluationError

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))

    class ForcedSuite:
        def __init__(self, eval_service):
            del eval_service

        def run_suite(self, **kwargs):
            del kwargs
            raise EvaluationError("boom")

    monkeypatch.setattr(evaluation_routes, "EvaluationSuiteService", ForcedSuite)
    response = app_client.post(
        "/api/evaluations/suite",
        json=_suite([{"execution": _execution("Write a welcome message")}]),
    )
    assert response.status_code == 500, response.text
    assert response.json()["detail"]["code"] == "evaluation_failed"
    assert response.json()["detail"]["message"] == "Prompt evaluation suite failed unexpectedly."
    assert "boom" not in response.text


# --- MODE A: existing PromptRuns ---


@pytest.mark.integration
def test_suite_existing_run(db_ready, monkeypatch, settings):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes
    from app.main import app

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    with TestClient(app) as client:
        prompt = client.post(
            "/api/prompts",
            json={
                "title": f"Evaluation suite run {uuid.uuid4().hex[:8]}",
                "idea": "Test multi-target evaluation over a persisted PromptRun.",
            },
        )
        assert prompt.status_code == 201, prompt.text
        test_run = client.post(
            "/api/testing/run",
            json={
                "prompt": "Write a welcome message",
                "provider": "fake",
                "model": "fake-model-1",
                "prompt_id": prompt.json()["id"],
            },
        )
        assert test_run.status_code == 200, test_run.text
        run_id = test_run.json()["run_id"]
        assert run_id is not None

        response = client.post(
            "/api/evaluations/suite",
            json=_suite([{"run_id": run_id}]),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        assert body["passed"] == 1
        assert body["evaluations"][0]["run_id"] == run_id
        assert body["evaluations"][0]["output"] == "Simulated answer to: Write a welcome message"

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
def test_suite_mixed_run_and_execution_order(db_ready, monkeypatch, settings):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes
    from app.main import app

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    with TestClient(app) as client:
        prompt = client.post(
            "/api/prompts",
            json={
                "title": f"Evaluation suite order {uuid.uuid4().hex[:8]}",
                "idea": "Test mixed run + execution target ordering.",
            },
        )
        assert prompt.status_code == 201, prompt.text
        test_run = client.post(
            "/api/testing/run",
            json={
                "prompt": "Write a welcome message",
                "provider": "fake",
                "model": "fake-model-1",
                "prompt_id": prompt.json()["id"],
            },
        )
        assert test_run.status_code == 200, test_run.text
        run_id = test_run.json()["run_id"]

        response = client.post(
            "/api/evaluations/suite",
            json=_suite(
                [
                    {"run_id": run_id},
                    {"execution": _execution("Write a haiku")},
                ]
            ),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 2
        assert body["passed"] == 2
        assert body["evaluations"][0]["run_id"] == run_id
        assert body["evaluations"][1]["run_id"] is None


@pytest.mark.integration
def test_suite_unknown_run_404(db_ready, monkeypatch, settings):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes
    from app.main import app

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    with TestClient(app) as client:
        response = client.post(
            "/api/evaluations/suite",
            json=_suite([{"run_id": str(uuid.uuid4())}]),
        )
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "Prompt run not found"


@pytest.mark.integration
def test_suite_ownership_first_mixed_unknown_run_404(db_ready, monkeypatch, settings):
    """A foreign run anywhere in the list fails the whole suite (ownership-first)."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes
    from app.main import app

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    with TestClient(app) as client:
        prompt = client.post(
            "/api/prompts",
            json={
                "title": f"Evaluation suite owner {uuid.uuid4().hex[:8]}",
                "idea": "Test ownership-first resolution over suite targets.",
            },
        )
        assert prompt.status_code == 201, prompt.text
        test_run = client.post(
            "/api/testing/run",
            json={
                "prompt": "Write a welcome message",
                "provider": "fake",
                "model": "fake-model-1",
                "prompt_id": prompt.json()["id"],
            },
        )
        assert test_run.status_code == 200, test_run.text
        run_id = test_run.json()["run_id"]

        # Valid run FIRST, unknown run SECOND: still a clean 404, no partial results.
        response = client.post(
            "/api/evaluations/suite",
            json=_suite(
                [
                    {"run_id": run_id},
                    {"run_id": str(uuid.uuid4())},
                ]
            ),
        )
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "Prompt run not found"


# --- schema integrity: the suite persists nothing ---


def test_no_suite_tables_in_metadata():
    """The evaluation suite domain registers no tables on the SQLAlchemy metadata."""
    table_names = set(Base.metadata.tables.keys())
    for name in (
        "evaluation_suites",
        "suite_targets",
        "suite_results",
        "evaluation_runs",
        "evaluations",
    ):
        assert name not in table_names


@pytest.mark.integration
def test_no_suite_tables_in_database(db_ready):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    table_names = set(inspect(engine).get_table_names())
    for name in (
        "evaluation_suites",
        "suite_targets",
        "suite_results",
        "evaluation_runs",
        "evaluations",
    ):
        assert name not in table_names


def test_promptrun_columns_unchanged():
    """PromptRun model is untouched by Phase 3G (only 3O's version_id exists)."""
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