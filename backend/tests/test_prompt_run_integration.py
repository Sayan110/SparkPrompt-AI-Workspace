import uuid

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import ai as ai_routes
from app.models import PromptRun
from app.core.database import SessionLocal, engine

from conftest import FailingProvider

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


def _create_prompt(client, suffix: str) -> dict:
    response = client.post(
        "/api/prompts",
        json={
            "title": f"AI gateway integration {suffix}",
            "idea": "Test that generating with AI persists a run.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _use_fake(monkeypatch, settings):
    from app.ai.registry import ProviderRegistry
    from conftest import FakeProvider

    registry = ProviderRegistry(settings)
    registry.register(FakeProvider(settings))
    monkeypatch.setattr(ai_routes, "_REGISTRY_CACHE", registry)


def test_generate_with_prompt_persists_run(db_ready, monkeypatch, settings):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.main import app
    from fastapi.testclient import TestClient

    _use_fake(monkeypatch, settings)

    with TestClient(app) as client:
        prompt = _create_prompt(client, uuid.uuid4().hex[:8])
        response = client.post(
            "/api/ai/generate",
            json={
                "provider": "fake",
                "model": "fake-model-1",
                "messages": [{"role": "user", "content": "Remember this haiku?"}],
                "metadata": {"prompt_id": str(prompt["id"])},
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["prompt_id"] == prompt["id"]
        assert body["run_id"]

        with SessionLocal() as db:
            run = db.get(PromptRun, uuid.UUID(body["run_id"]))
            assert run is not None
            assert run.prompt_id == uuid.UUID(prompt["id"])
            assert run.status == "success"
            assert run.provider == "fake"
            assert run.model == "fake-model-1"
            assert run.output_text == body["text"]
            assert run.latency_ms is not None
            assert run.finish_reason == "stop"
            assert run.usage_json == {
                "prompt_tokens": 10,
                "completion_tokens": 25,
                "total_tokens": 35,
            }
            assert run.input_snapshot["messages"][0]["content"] == "Remember this haiku?"

        # cleanup
        deleted = client.delete(f"/api/prompts/{prompt['id']}")
        assert deleted.status_code == 200


def test_generate_failure_persists_error_run(db_ready, monkeypatch, settings):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.main import app
    from fastapi.testclient import TestClient

    from app.ai.registry import ProviderRegistry

    registry = ProviderRegistry(settings)
    registry.register(FailingProvider(settings))
    monkeypatch.setattr(ai_routes, "_REGISTRY_CACHE", registry)

    with TestClient(app) as client:
        prompt = _create_prompt(client, uuid.uuid4().hex[:8])
        response = client.post(
            "/api/ai/generate",
            json={
                "provider": "fake",
                "messages": [{"role": "user", "content": "force failure"}],
                "metadata": {"prompt_id": str(prompt["id"])},
            },
        )
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "provider_error"

        with SessionLocal() as db:
            runs = list(
                db.scalars(select(PromptRun).where(PromptRun.prompt_id == uuid.UUID(prompt["id"])))
            )
            assert len(runs) == 1
            run = runs[0]
            assert run.status == "error"
            assert run.error
            assert run.provider == "fake"
            assert run.output_text is None

        deleted = client.delete(f"/api/prompts/{prompt['id']}")
        assert deleted.status_code == 200


def test_schema_verification_is_readonly():
    """Schema checks are idempotent and never mutate (Phase 4B replaces the old
    runtime schema-update idempotency test: the mutating path no longer exists;
    migration-level idempotency is covered by tests/test_migrations.py)."""
    if not _db_reachable():
        pytest.skip("Postgres not reachable")
    from sqlalchemy import text

    from app.core.database import verify_schema_ready

    def _revision():
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()

    revision_before = _revision()
    verify_schema_ready()
    verify_schema_ready()  # second call must not raise
    assert _revision() == revision_before  # verification never touches schema state

    assert "prompt_runs" in inspect(engine).get_table_names()