"""Phase 3D endpoint tests for POST /api/testing/run.

The route uses the real AIGateway with provider adapters registered into the test
registry (the same pattern as test_ai_routes.py / test_intelligence_routes.py). No
live provider keys, no Ollama. The route returns the normalized ``PromptTestResult``
and reuses the established error envelope, so provider failures surface identically
to the intelligence routes.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.ai.registry import ProviderRegistry
from app.ai.types import AIResponse, AIUsage
from app.core.config import Settings

from conftest import FailingProvider, FakeProvider, UnavailableProvider
from app.core.database import SessionLocal, engine
from app.models import PromptRun


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


def _registry_with(provider_cls, settings: Settings, **kwargs) -> ProviderRegistry:
    registry = ProviderRegistry(settings)
    registry.register(provider_cls(settings, **kwargs))
    return registry


def _use_registry(monkeypatch, ai_routes, registry):
    monkeypatch.setattr(ai_routes, "_REGISTRY_CACHE", registry)


class NoDefaultProvider(FakeProvider):
    """A provider whose model cannot be resolved unless one is given explicitly."""

    name = "No Default Model Fake"

    def default_model(self) -> str | None:
        return None


class NoUsageProvider(FakeProvider):
    """A provider that never reports token usage."""

    name = "No Usage Fake"

    def generate(self, request):
        return AIResponse(
            text="Simulated answer to: " + request.messages[-1].content,
            provider=self.id,
            model=request.model,
            finish_reason="stop",
            usage=None,
            request_id=request.request_id,
        )


def _create_prompt(client, suffix: str) -> dict:
    response = client.post(
        "/api/prompts",
        json={
            "title": f"Testing run {suffix}",
            "idea": "Test that test executions persist as PromptRuns.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- success paths ---


def test_run_success(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post("/api/testing/run", json={"prompt": "Write a haiku"})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["output"] == "Simulated answer to: Write a haiku"
    assert body["provider"] == "fake"
    assert body["model"] == "fake-model-1"
    assert body["finish_reason"] == "stop"
    assert body["latency_ms"] is not None
    assert body["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 25,
        "total_tokens": 35,
    }
    # No saved prompt attached -> no PromptRun metadata in the bare result.
    assert body["prompt_id"] is None
    assert body["run_id"] is None
    assert body["request_id"]


def test_run_accepts_test_input(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/testing/run",
        json={"prompt": "Write a product description", "input": "Product: wireless headphones"},
    )
    assert response.status_code == 200, response.text
    assert "INPUT:" in response.json()["output"]
    assert "Product: wireless headphones" in response.json()["output"]


def test_run_accepts_explicit_provider_and_model(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/testing/run",
        json={"prompt": "Write a haiku", "provider": "fake", "model": "fake-model-2"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["model"] == "fake-model-2"


def test_run_usage_unavailable(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(NoUsageProvider, settings))
    response = app_client.post("/api/testing/run", json={"prompt": "Write a haiku"})
    assert response.status_code == 200, response.text
    assert response.json()["usage"] is None


# --- endpoint validation ---


def test_run_empty_prompt_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post("/api/testing/run", json={"prompt": ""})
    assert response.status_code == 422


def test_run_blank_prompt_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post("/api/testing/run", json={"prompt": "   "})
    assert response.status_code == 422


def test_run_oversized_prompt_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post("/api/testing/run", json={"prompt": "a" * 20_001})
    assert response.status_code == 422


def test_run_oversized_input_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/testing/run", json={"prompt": "Write a haiku", "input": "x" * 20_001}
    )
    assert response.status_code == 422


def test_run_provider_too_long_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/testing/run", json={"prompt": "Write a haiku", "provider": "p" * 41}
    )
    assert response.status_code == 422


# --- provider / gateway failure paths ---


def test_run_unknown_provider(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post(
        "/api/testing/run", json={"prompt": "Write a haiku", "provider": "nope"}
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "unknown_provider"


def test_run_provider_unavailable(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(UnavailableProvider, settings))
    response = app_client.post("/api/testing/run", json={"prompt": "Write a haiku"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "provider_unavailable"


def test_run_missing_default_model_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(NoDefaultProvider, settings))
    response = app_client.post(
        "/api/testing/run",
        json={"prompt": "Write a haiku", "provider": "fake"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_request"


def test_run_gateway_error_normalized(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FailingProvider, settings))
    response = app_client.post("/api/testing/run", json={"prompt": "Write a haiku"})
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "provider_error"
    # Normalized message only — never a raw stack trace or upstream body.
    assert "Traceback" not in response.text
    assert "File " not in response.text


def test_run_unexpected_error_sanitized(monkeypatch, settings):
    """Unexpected server errors surface as a generic 500 with no internal detail."""
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes
    from app.main import app
    from conftest import ExplodingProvider

    _use_registry(monkeypatch, ai_routes, _registry_with(ExplodingProvider, settings))
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/api/testing/run", json={"prompt": "hi"})
    assert response.status_code == 500
    assert "unexpected-internal-detail" not in response.text
    assert "RuntimeError" not in response.text


def test_run_domain_validation_error_sanitized(monkeypatch, settings, app_client):
    """The route's domain-error branch maps to a sanitized 400 envelope.

    Normally unreachable via HTTP (Pydantic rejects bad input first), this branch is
    forced here to prove defensive handling for direct service errors.
    """
    from app.api.routes import ai as ai_routes
    from app.api.routes import testing as testing_routes
    from app.testing import PromptTestingValidationError

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))

    class ForcedService:
        def __init__(self, gateway):
            self._gateway = gateway

        def run(self, **kwargs):
            raise PromptTestingValidationError("forced validation failure")

    monkeypatch.setattr(testing_routes, "PromptTestingService", ForcedService)
    response = app_client.post("/api/testing/run", json={"prompt": "Write a haiku"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_test_request"
    assert response.json()["detail"]["message"] == "forced validation failure"


# --- PromptRun integration (real Postgres; skipped when unreachable) ---


@pytest.mark.integration
def test_run_unknown_prompt_id_rejected(db_ready, monkeypatch, settings):
    """A prompt_id with no matching prompt must fail as 404, never reach the gateway."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.main import app
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    with TestClient(app) as client:
        response = client.post(
            "/api/testing/run",
            json={"prompt": "Write a haiku", "prompt_id": str(uuid.uuid4())},
        )
        assert response.status_code == 404
        assert "Prompt not found" in response.text


@pytest.mark.integration
def test_run_persists_promptrun_on_success(db_ready, monkeypatch, settings):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.main import app
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    with TestClient(app) as client:
        prompt = _create_prompt(client, uuid.uuid4().hex[:8])
        response = client.post(
            "/api/testing/run",
            json={
                "prompt": "Write a welcome message",
                "provider": "fake",
                "model": "fake-model-1",
                "prompt_id": prompt["id"],
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["run_id"]
        assert body["prompt_id"] == prompt["id"]

        db = SessionLocal()
        try:
            run = db.get(PromptRun, uuid.UUID(body["run_id"]))
            assert run is not None
            assert str(run.prompt_id) == prompt["id"]
            assert run.status == "success"
            # The gateway records the requested provider/model verbatim on the run.
            assert run.provider == "fake"
            assert run.model == "fake-model-1"
            assert run.output_text == body["output"]
            assert run.input_snapshot["messages"][0] == {
                "role": "user",
                "content": "Write a welcome message",
            }
        finally:
            db.close()


@pytest.mark.integration
def test_run_persists_error_run_on_failure(db_ready, monkeypatch, settings):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.main import app
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FailingProvider, settings))
    with TestClient(app) as client:
        prompt = _create_prompt(client, uuid.uuid4().hex[:8])
        response = client.post(
            "/api/testing/run",
            json={"prompt": "Write a welcome message", "prompt_id": prompt["id"]},
        )
        assert response.status_code == 502

        db = SessionLocal()
        try:
            run = (
                db.query(PromptRun)
                .filter(PromptRun.prompt_id == uuid.UUID(prompt["id"]))
                .order_by(PromptRun.created_at.desc())
                .first()
            )
            assert run is not None
            assert run.status == "error"
            assert run.error
        finally:
            db.close()