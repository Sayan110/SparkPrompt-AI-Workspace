import json

from app.ai.registry import ProviderRegistry

from conftest import (
    ExplodingProvider,
    FailingProvider,
    UnavailableProvider,
    UnconfiguredProvider,
    make_request,
)
from app.core.config import Settings


# --- routes need a registry; app_client already monkeypatches _REGISTRY_CACHE. ---


def _registry_with(provider_cls, settings: Settings) -> ProviderRegistry:
    registry = ProviderRegistry(settings)
    registry.register(provider_cls(settings))
    return registry


def _use_registry(monkeypatch, ai_routes, registry):
    monkeypatch.setattr(ai_routes, "_REGISTRY_CACHE", registry)


def test_status_ready(app_client):
    response = app_client.get("/api/ai/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["phase"] == "gateway"
    assert body["providers"]["fake"] == "available"
    assert body["ai_runtime"] == "configured"


def test_status_not_configured_when_no_provider_configured(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    registry = _registry_with(UnconfiguredProvider, settings)
    _use_registry(monkeypatch, ai_routes, registry)
    response = app_client.get("/api/ai/status")
    assert response.status_code == 200
    body = response.json()
    assert body["ai_runtime"] == "not_configured"
    assert body["providers"]["fake"] == "unavailable"


def test_providers_listing(app_client):
    response = app_client.get("/api/ai/providers")
    assert response.status_code == 200
    providers = response.json()
    assert any(provider["id"] == "fake" for provider in providers)
    fake = next(provider for provider in providers if provider["id"] == "fake")
    assert fake["available"] is True
    assert fake["configured"] is True
    assert fake["streaming"] is True
    assert "fake-model-2" in fake["models"]
    assert fake["default_model"] == "fake-model-1"


def test_models_listing(app_client):
    response = app_client.get("/api/ai/models")
    assert response.status_code == 200
    entries = {entry["provider"]: entry["models"] for entry in response.json()}
    assert "fake" in entries
    assert "fake-model-1" in entries["fake"]


def test_generate_success(app_client):
    response = app_client.post(
        "/api/ai/generate",
        json={
            "provider": "fake",
            "model": "fake-model-1",
            "messages": [{"role": "user", "content": "Write a haiku"}],
            "temperature": 0.3,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "fake"
    assert body["model"] == "fake-model-1"
    assert body["text"]
    assert body["usage"]["total_tokens"] == 35
    assert body["latency_ms"] is not None
    assert body["request_id"]
    assert body["prompt_id"] is None
    assert body["run_id"] is None


def test_generate_unknown_provider(app_client):
    response = app_client.post(
        "/api/ai/generate",
        json={
            "provider": "missing",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "unknown_provider"


def test_generate_unavailable_provider(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(UnavailableProvider, settings))
    response = app_client.post(
        "/api/ai/generate",
        json={"provider": "fake", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["code"] == "provider_unavailable"


def test_generate_provider_error_normalized(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FailingProvider, settings))
    response = app_client.post(
        "/api/ai/generate",
        json={"provider": "fake", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 502
    body = response.json()["detail"]
    assert body["code"] == "provider_error"
    assert body["provider"] == "fake"
    assert "upstream" in body["message"]
    assert "stacktrace" not in body["message"].lower()


def test_generate_invalid_payload_rejected(app_client):
    response = app_client.post(
        "/api/ai/generate",
        json={"provider": "fake", "messages": [{"role": "user", "content": ""}]},
    )
    assert response.status_code == 422

    response = app_client.post(
        "/api/ai/generate",
        json={"provider": "fake", "messages": [{"role": "user", "content": "hi"}], "temperature": 5},
    )
    assert response.status_code == 422


def test_generate_stream_sse(app_client):
    with app_client.stream(
        "POST",
        "/api/ai/generate",
        json={
            "provider": "fake",
            "model": "fake-model-1",
            "messages": [{"role": "user", "content": "stream me"}],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        content = "".join(response.iter_text())
    assert "data: " in content
    assert '"type": "delta"' in content
    assert '"type": "done"' in content


def test_generate_stream_error_event(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FailingProvider, settings))
    with app_client.stream(
        "POST",
        "/api/ai/generate",
        json={
            "provider": "fake",
            "messages": [{"role": "user", "content": "boom"}],
            "stream": True,
        },
    ) as response:
        content = "".join(response.iter_text())
    assert '"type": "error"' in content
    assert '"code": "provider_error"' in content


def _parse_sse(content: str) -> list[dict]:
    events = []
    for block in content.split("\n\n"):
        block = block.strip()
        if not block.startswith("data: "):
            continue
        events.append(json.loads(block[len("data: ") :]))
    return events


def test_generate_stream_uses_resolved_default_provider_model(app_client):
    with app_client.stream(
        "POST",
        "/api/ai/generate",
        json={"messages": [{"role": "user", "content": "hey"}], "stream": True},
    ) as response:
        assert response.status_code == 200
        content = "".join(response.iter_text())
    events = _parse_sse(content)
    assert events
    for event in events:
        assert event.get("provider") == "fake"
        assert event.get("model") == "fake-model-1"


def test_generate_stream_done_includes_latency(app_client):
    with app_client.stream(
        "POST",
        "/api/ai/generate",
        json={
            "provider": "fake",
            "model": "fake-model-1",
            "messages": [{"role": "user", "content": "stream me"}],
            "stream": True,
        },
    ) as response:
        content = "".join(response.iter_text())
    events = _parse_sse(content)
    done = events[-1]
    assert done["type"] == "done"
    assert done.get("latency_ms") is not None
    assert done["provider"] == "fake"
    assert done["model"] == "fake-model-1"


def test_generate_stream_unexpected_error_sanitized(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(ExplodingProvider, settings))
    with app_client.stream(
        "POST",
        "/api/ai/generate",
        json={"provider": "fake", "messages": [{"role": "user", "content": "boom"}], "stream": True},
    ) as response:
        content = "".join(response.iter_text())
    events = _parse_sse(content)
    assert events
    last = events[-1]
    assert last["type"] == "error"
    assert last["code"] == "internal_error"
    assert "unexpected-internal-detail" not in content
    assert "RuntimeError" not in content
    assert last["message"] == "The AI request failed unexpectedly. Please try again."