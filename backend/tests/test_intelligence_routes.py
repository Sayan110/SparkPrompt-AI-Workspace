"""Phase 3B + 3C endpoint tests for POST /api/intelligence/{analyze,enhance,create}.

The routes use the real AIGateway with provider adapters registered into the test
registry (the same pattern as test_ai_routes.py). No live provider keys, no Ollama.
Enhance and create reuse the exact error envelope analyze established in Phase 3B.
"""

import json

from app.ai.registry import ProviderRegistry
from app.ai.types import AIResponse, AIUsage

from conftest import FailingProvider, FakeProvider, UnavailableProvider
from app.core.config import Settings

VALID_ANALYSIS_PAYLOAD = {
    "clarity": "The goal is understandable.",
    "specificity": "The desired output is precise.",
    "context": "Background and audience are provided.",
    "constraints": "Tone and limits are stated.",
    "output_format": "The response shape is defined.",
    "missing_information": ["budget", "tone"],
    "suggestions": ["Add an example output."],
}

VALID_ENHANCEMENT_PAYLOAD = {
    "original_prompt": "Tighten this welcome message.",
    "enhanced_prompt": (
        "Write a warm, two-sentence welcome message that names the product and "
        "invites the reader to take the next step."
    ),
    "improvements": ["Named the product.", "Gave the message a concrete next step."],
}

VALID_CREATION_PAYLOAD = {
    "prompt": (
        "Act as a meticulous editor. Rewrite the input to be clearer, more "
        "specific, and more actionable."
    ),
    "rationale": (
        "A role, an explicit task, and a concrete outcome frame the request."
    ),
}


class JsonAnalysisProvider(FakeProvider):
    """A fake provider that returns a valid structured analysis document."""

    id = "fake"
    name = "JSON Analysis Fake"

    def __init__(self, settings: Settings, payload: dict | None = None):
        super().__init__(settings)
        self.payload = payload or VALID_ANALYSIS_PAYLOAD

    def generate(self, request):
        return AIResponse(
            text=json.dumps(self.payload),
            provider=self.id,
            model=request.model,
            finish_reason="stop",
            usage=AIUsage(prompt_tokens=10, completion_tokens=25, total_tokens=35),
            request_id=request.request_id,
        )


class MalformedAnalysisProvider(JsonAnalysisProvider):
    id = "fake"
    name = "Malformed JSON Fake"

    def generate(self, request):
        return AIResponse(
            text="I am sorry, but I cannot help with that.",
            provider=self.id,
            model=request.model,
            finish_reason="stop",
            usage=None,
            request_id=request.request_id,
        )


class PartialAnalysisProvider(JsonAnalysisProvider):
    id = "fake"
    name = "Partial JSON Fake"

    def generate(self, request):
        payload = dict(self.payload)
        payload.pop("suggestions")
        return AIResponse(
            text=json.dumps(payload),
            provider=self.id,
            model=request.model,
            finish_reason="stop",
            usage=None,
            request_id=request.request_id,
        )


class ExplodingAnalysisProvider(JsonAnalysisProvider):
    id = "fake"
    name = "Exploding JSON Fake"

    def generate(self, request):
        raise RuntimeError("unexpected-internal-detail from analyze")


class JsonEnhancementProvider(FakeProvider):
    """A fake provider that returns a valid structured enhancement document."""

    id = "fake"
    name = "JSON Enhancement Fake"

    def __init__(self, settings: Settings, payload: dict | None = None):
        super().__init__(settings)
        self.payload = payload or VALID_ENHANCEMENT_PAYLOAD

    def generate(self, request):
        return AIResponse(
            text=json.dumps(self.payload),
            provider=self.id,
            model=request.model,
            finish_reason="stop",
            usage=AIUsage(prompt_tokens=10, completion_tokens=25, total_tokens=35),
            request_id=request.request_id,
        )


class MalformedEnhancementProvider(JsonEnhancementProvider):
    id = "fake"
    name = "Malformed Enhancement Fake"

    def generate(self, request):
        return AIResponse(
            text="I am sorry, but I cannot help with that.",
            provider=self.id,
            model=request.model,
            finish_reason="stop",
            usage=None,
            request_id=request.request_id,
        )


class PartialEnhancementProvider(JsonEnhancementProvider):
    id = "fake"
    name = "Partial Enhancement Fake"

    def generate(self, request):
        payload = dict(self.payload)
        payload.pop("improvements")
        return AIResponse(
            text=json.dumps(payload),
            provider=self.id,
            model=request.model,
            finish_reason="stop",
            usage=None,
            request_id=request.request_id,
        )


class ExtraKeyEnhancementProvider(JsonEnhancementProvider):
    id = "fake"
    name = "Extra Key Enhancement Fake"

    def generate(self, request):
        payload = dict(self.payload)
        payload["notes"] = "leaked metadata"
        return AIResponse(
            text=json.dumps(payload),
            provider=self.id,
            model=request.model,
            finish_reason="stop",
            usage=None,
            request_id=request.request_id,
        )


class EmptyEnhancedPromptProvider(JsonEnhancementProvider):
    id = "fake"
    name = "Empty Enhanced Prompt Fake"

    def generate(self, request):
        payload = dict(self.payload)
        payload["enhanced_prompt"] = ""
        return AIResponse(
            text=json.dumps(payload),
            provider=self.id,
            model=request.model,
            finish_reason="stop",
            usage=None,
            request_id=request.request_id,
        )


class ExplodingEnhancementProvider(JsonEnhancementProvider):
    id = "fake"
    name = "Exploding Enhancement Fake"

    def generate(self, request):
        raise RuntimeError("unexpected-internal-detail from enhance")


class JsonCreationProvider(FakeProvider):
    """A fake provider that returns a valid structured creation document."""

    id = "fake"
    name = "JSON Creation Fake"

    def __init__(self, settings: Settings, payload: dict | None = None):
        super().__init__(settings)
        self.payload = payload or VALID_CREATION_PAYLOAD

    def generate(self, request):
        return AIResponse(
            text=json.dumps(self.payload),
            provider=self.id,
            model=request.model,
            finish_reason="stop",
            usage=AIUsage(prompt_tokens=10, completion_tokens=25, total_tokens=35),
            request_id=request.request_id,
        )


class MalformedCreationProvider(JsonCreationProvider):
    id = "fake"
    name = "Malformed Creation Fake"

    def generate(self, request):
        return AIResponse(
            text="I am sorry, but I cannot help with that.",
            provider=self.id,
            model=request.model,
            finish_reason="stop",
            usage=None,
            request_id=request.request_id,
        )


class PartialCreationProvider(JsonCreationProvider):
    id = "fake"
    name = "Partial Creation Fake"

    def generate(self, request):
        payload = dict(self.payload)
        payload.pop("rationale")
        return AIResponse(
            text=json.dumps(payload),
            provider=self.id,
            model=request.model,
            finish_reason="stop",
            usage=None,
            request_id=request.request_id,
        )


class EmptyRationaleProvider(JsonCreationProvider):
    id = "fake"
    name = "Empty Rationale Fake"

    def generate(self, request):
        payload = dict(self.payload)
        payload["rationale"] = ""
        return AIResponse(
            text=json.dumps(payload),
            provider=self.id,
            model=request.model,
            finish_reason="stop",
            usage=None,
            request_id=request.request_id,
        )


class ExplodingCreationProvider(JsonCreationProvider):
    id = "fake"
    name = "Exploding Creation Fake"

    def generate(self, request):
        raise RuntimeError("unexpected-internal-detail from create")


def _registry_with(provider_cls, settings: Settings, **kwargs) -> ProviderRegistry:
    registry = ProviderRegistry(settings)
    registry.register(provider_cls(settings, **kwargs))
    return registry


def _use_registry(monkeypatch, ai_routes, registry):
    monkeypatch.setattr(ai_routes, "_REGISTRY_CACHE", registry)


# --- success paths ---


def test_analyze_success(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(JsonAnalysisProvider, settings))
    response = app_client.post(
        "/api/intelligence/analyze", json={"prompt": "Build a landing page"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["clarity"] == "The goal is understandable."
    assert body["missing_information"] == ["budget", "tone"]
    assert body["suggestions"] == ["Add an example output."]
    # The response is the bare PromptAnalysis contract: no run/prompt wrappers.
    assert "prompt_id" not in body
    assert "run_id" not in body
    assert "text" not in body


def test_analyze_accepts_provider_and_model(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(JsonAnalysisProvider, settings))
    response = app_client.post(
        "/api/intelligence/analyze",
        json={
            "prompt": "Build a landing page",
            "provider": "fake",
            "model": "fake-model-1",
        },
    )
    assert response.status_code == 200
    assert response.json()["clarity"]


def test_analyze_uses_server_default_when_provider_omitted(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(JsonAnalysisProvider, settings))
    response = app_client.post(
        "/api/intelligence/analyze",
        json={"prompt": "Build a landing page", "model": "fake-model-1"},
    )
    assert response.status_code == 200
    assert response.json()["clarity"]


# --- input validation (schema level -> 422) ---


def test_analyze_invalid_payload_rejected(app_client):
    empty = app_client.post("/api/intelligence/analyze", json={"prompt": ""})
    assert empty.status_code == 422

    blank = app_client.post("/api/intelligence/analyze", json={"prompt": "   "})
    assert blank.status_code == 422

    missing = app_client.post("/api/intelligence/analyze", json={})
    assert missing.status_code == 422

    oversized = app_client.post("/api/intelligence/analyze", json={"prompt": "x" * 20_001})
    assert oversized.status_code == 422


def test_analyze_provider_too_long_rejected(app_client):
    response = app_client.post(
        "/api/intelligence/analyze",
        json={"prompt": "hi", "provider": "x" * 41},
    )
    assert response.status_code == 422


# --- AI response normalization (-> 502 invalid_ai_response) ---


def test_analyze_malformed_ai_response_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(MalformedAnalysisProvider, settings))
    response = app_client.post("/api/intelligence/analyze", json={"prompt": "hi"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_ai_response"
    assert "cannot help" not in detail["message"]


def test_analyze_partial_ai_response_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(PartialAnalysisProvider, settings))
    response = app_client.post("/api/intelligence/analyze", json={"prompt": "hi"})
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "invalid_ai_response"


# --- gateway/provider failures reuse the Phase 2 error mapping ---


def test_analyze_provider_unavailable(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(UnavailableProvider, settings))
    response = app_client.post("/api/intelligence/analyze", json={"prompt": "hi"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "provider_unavailable"


def test_analyze_unknown_provider(app_client):
    response = app_client.post(
        "/api/intelligence/analyze", json={"prompt": "hi", "provider": "missing"}
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "unknown_provider"


def test_analyze_provider_error_normalized(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FailingProvider, settings))
    response = app_client.post("/api/intelligence/analyze", json={"prompt": "hi"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "provider_error"
    assert detail["provider"] == "fake"
    assert "upstream" in detail["message"]
    assert "stacktrace" not in detail["message"].lower()


def test_analyze_unexpected_error_sanitized(monkeypatch, settings):
    """Unexpected server errors must surface as a generic 500 with no internal detail."""
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes
    from app.main import app

    _use_registry(monkeypatch, ai_routes, _registry_with(ExplodingAnalysisProvider, settings))
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/api/intelligence/analyze", json={"prompt": "hi"})
    assert response.status_code == 500
    assert "unexpected-internal-detail" not in response.text
    assert "RuntimeError" not in response.text


def test_analyze_endpoint_is_provider_independent(app_client, monkeypatch, settings):
    """The endpoint succeeds against an injected fake provider — no external AI involved."""
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(JsonAnalysisProvider, settings))
    response = app_client.post(
        "/api/intelligence/analyze",
        json={"prompt": "Write a two-sentence welcome message"},
    )
    assert response.status_code == 200
    assert response.json()["suggestions"]


# --- Phase 3C: POST /api/intelligence/enhance ---


def test_enhance_success(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(JsonEnhancementProvider, settings))
    response = app_client.post(
        "/api/intelligence/enhance", json={"prompt": "Tighten this welcome message."}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["original_prompt"] == "Tighten this welcome message."
    assert "two-sentence welcome message" in body["enhanced_prompt"]
    assert body["improvements"] == [
        "Named the product.",
        "Gave the message a concrete next step.",
    ]
    # The response is the bare PromptEnhancement contract: no run/prompt wrappers.
    assert "prompt_id" not in body
    assert "run_id" not in body
    assert "text" not in body


def test_enhance_accepts_provider_and_model(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(JsonEnhancementProvider, settings))
    response = app_client.post(
        "/api/intelligence/enhance",
        json={
            "prompt": "Tighten this welcome message.",
            "provider": "fake",
            "model": "fake-model-1",
        },
    )
    assert response.status_code == 200
    assert response.json()["enhanced_prompt"]


def test_enhance_invalid_payload_rejected(app_client):
    empty = app_client.post("/api/intelligence/enhance", json={"prompt": ""})
    assert empty.status_code == 422

    blank = app_client.post("/api/intelligence/enhance", json={"prompt": "   "})
    assert blank.status_code == 422

    missing = app_client.post("/api/intelligence/enhance", json={})
    assert missing.status_code == 422

    oversized = app_client.post("/api/intelligence/enhance", json={"prompt": "x" * 20_001})
    assert oversized.status_code == 422


def test_enhance_provider_too_long_rejected(app_client):
    response = app_client.post(
        "/api/intelligence/enhance",
        json={"prompt": "hi", "provider": "x" * 41},
    )
    assert response.status_code == 422


def test_enhance_malformed_ai_response_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(
        monkeypatch, ai_routes, _registry_with(MalformedEnhancementProvider, settings)
    )
    response = app_client.post("/api/intelligence/enhance", json={"prompt": "hi"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_ai_response"
    assert "cannot help" not in detail["message"]


def test_enhance_partial_ai_response_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(
        monkeypatch, ai_routes, _registry_with(PartialEnhancementProvider, settings)
    )
    response = app_client.post("/api/intelligence/enhance", json={"prompt": "hi"})
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "invalid_ai_response"


def test_enhance_extra_keys_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(
        monkeypatch, ai_routes, _registry_with(ExtraKeyEnhancementProvider, settings)
    )
    response = app_client.post("/api/intelligence/enhance", json={"prompt": "hi"})
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "invalid_ai_response"


def test_enhance_empty_enhanced_prompt_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(
        monkeypatch, ai_routes, _registry_with(EmptyEnhancedPromptProvider, settings)
    )
    response = app_client.post("/api/intelligence/enhance", json={"prompt": "hi"})
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "invalid_ai_response"


def test_enhance_provider_unavailable(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(UnavailableProvider, settings))
    response = app_client.post("/api/intelligence/enhance", json={"prompt": "hi"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "provider_unavailable"


def test_enhance_unknown_provider(app_client):
    response = app_client.post(
        "/api/intelligence/enhance", json={"prompt": "hi", "provider": "missing"}
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "unknown_provider"


def test_enhance_provider_error_normalized(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FailingProvider, settings))
    response = app_client.post("/api/intelligence/enhance", json={"prompt": "hi"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "provider_error"
    assert detail["provider"] == "fake"
    assert "upstream" in detail["message"]
    assert "stacktrace" not in detail["message"].lower()


def test_enhance_unexpected_error_sanitized(monkeypatch, settings):
    """Unexpected server errors must surface as a generic 500 with no internal detail."""
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes
    from app.main import app

    _use_registry(
        monkeypatch, ai_routes, _registry_with(ExplodingEnhancementProvider, settings)
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/api/intelligence/enhance", json={"prompt": "hi"})
    assert response.status_code == 500
    assert "unexpected-internal-detail" not in response.text
    assert "RuntimeError" not in response.text


def test_enhance_endpoint_is_provider_independent(app_client, monkeypatch, settings):
    """The endpoint succeeds against an injected fake provider — no external AI involved."""
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(JsonEnhancementProvider, settings))
    response = app_client.post(
        "/api/intelligence/enhance",
        json={"prompt": "Write a two-sentence welcome message"},
    )
    assert response.status_code == 200
    assert response.json()["improvements"]


# --- Phase 3C: POST /api/intelligence/create ---


def test_create_success(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(JsonCreationProvider, settings))
    response = app_client.post(
        "/api/intelligence/create",
        json={"goal": "A tool that turns ideas into prompts."},
    )
    assert response.status_code == 200
    body = response.json()
    assert "meticulous editor" in body["prompt"]
    assert "role" in body["rationale"]
    # The response is the bare PromptCreation contract: no run/prompt wrappers.
    assert "prompt_id" not in body
    assert "run_id" not in body
    assert "text" not in body


def test_create_accepts_context(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(JsonCreationProvider, settings))
    response = app_client.post(
        "/api/intelligence/create",
        json={
            "goal": "A tool that turns ideas into prompts.",
            "context": "For a founder building a CLI, not a web app.",
        },
    )
    assert response.status_code == 200
    assert response.json()["prompt"]


def test_create_accepts_provider_and_model(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(JsonCreationProvider, settings))
    response = app_client.post(
        "/api/intelligence/create",
        json={
            "goal": "A tool that turns ideas into prompts.",
            "provider": "fake",
            "model": "fake-model-1",
        },
    )
    assert response.status_code == 200
    assert response.json()["prompt"]


def test_create_invalid_payload_rejected(app_client):
    empty = app_client.post("/api/intelligence/create", json={"goal": ""})
    assert empty.status_code == 422

    blank = app_client.post("/api/intelligence/create", json={"goal": "   "})
    assert blank.status_code == 422

    missing = app_client.post("/api/intelligence/create", json={})
    assert missing.status_code == 422

    oversized = app_client.post("/api/intelligence/create", json={"goal": "x" * 20_001})
    assert oversized.status_code == 422


def test_create_oversized_context_rejected(app_client):
    response = app_client.post(
        "/api/intelligence/create",
        json={"goal": "hi", "context": "x" * 20_001},
    )
    assert response.status_code == 422


def test_create_provider_too_long_rejected(app_client):
    response = app_client.post(
        "/api/intelligence/create",
        json={"goal": "hi", "provider": "x" * 41},
    )
    assert response.status_code == 422


def test_create_malformed_ai_response_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(MalformedCreationProvider, settings))
    response = app_client.post("/api/intelligence/create", json={"goal": "hi"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_ai_response"
    assert "cannot help" not in detail["message"]


def test_create_partial_ai_response_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(PartialCreationProvider, settings))
    response = app_client.post("/api/intelligence/create", json={"goal": "hi"})
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "invalid_ai_response"


def test_create_empty_rationale_rejected(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(EmptyRationaleProvider, settings))
    response = app_client.post("/api/intelligence/create", json={"goal": "hi"})
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "invalid_ai_response"


def test_create_provider_unavailable(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(UnavailableProvider, settings))
    response = app_client.post("/api/intelligence/create", json={"goal": "hi"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "provider_unavailable"


def test_create_unknown_provider(app_client):
    response = app_client.post(
        "/api/intelligence/create", json={"goal": "hi", "provider": "missing"}
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "unknown_provider"


def test_create_provider_error_normalized(app_client, monkeypatch, settings):
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FailingProvider, settings))
    response = app_client.post("/api/intelligence/create", json={"goal": "hi"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "provider_error"
    assert detail["provider"] == "fake"
    assert "upstream" in detail["message"]
    assert "stacktrace" not in detail["message"].lower()


def test_create_unexpected_error_sanitized(monkeypatch, settings):
    """Unexpected server errors must surface as a generic 500 with no internal detail."""
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes
    from app.main import app

    _use_registry(
        monkeypatch, ai_routes, _registry_with(ExplodingCreationProvider, settings)
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/api/intelligence/create", json={"goal": "hi"})
    assert response.status_code == 500
    assert "unexpected-internal-detail" not in response.text
    assert "RuntimeError" not in response.text


def test_create_endpoint_is_provider_independent(app_client, monkeypatch, settings):
    """The endpoint succeeds against an injected fake provider — no external AI involved."""
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(JsonCreationProvider, settings))
    response = app_client.post(
        "/api/intelligence/create",
        json={"goal": "A tool that turns ideas into prompts."},
    )
    assert response.status_code == 200
    assert response.json()["prompt"]