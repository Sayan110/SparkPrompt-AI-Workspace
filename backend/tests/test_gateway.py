import uuid

import pytest

from app.ai.errors import AIGatewayError, AIProviderError, ErrorCode
from app.ai.gateway import AIGateway
from app.ai.registry import ProviderRegistry

from conftest import (
    ExplodingProvider,
    FailingProvider,
    NonStreamingProvider,
    UnavailableProvider,
    make_request,
)


def test_gateway_success_normalizes_response(gateway):
    response, run = gateway.generate(None, make_request())
    assert response.provider == "fake"
    assert response.model == "fake-model-1"
    assert response.finish_reason == "stop"
    assert response.usage.prompt_tokens == 10
    assert response.usage.total_tokens == 35
    assert response.latency_ms is not None
    assert isinstance(response.text, str) and response.text
    assert run is None  # no prompt linked -> nothing persisted


def test_gateway_unknown_provider(gateway):
    with pytest.raises(AIGatewayError) as exc:
        gateway.generate(None, make_request(provider="does-not-exist"))
    assert exc.value.code == ErrorCode.UNKNOWN_PROVIDER


def test_gateway_unavailable_provider(settings):
    registry = ProviderRegistry(settings)
    registry.register(UnavailableProvider(settings))
    gateway = AIGateway(registry)
    with pytest.raises(AIProviderError) as exc:
        gateway.generate(None, make_request(provider="fake"))
    assert exc.value.code == ErrorCode.PROVIDER_UNAVAILABLE


def test_gateway_propagates_provider_error(settings):
    registry = ProviderRegistry(settings)
    registry.register(FailingProvider(settings))
    gateway = AIGateway(registry)
    with pytest.raises(AIProviderError) as exc:
        gateway.generate(None, make_request(provider="fake"))
    assert exc.value.code == ErrorCode.PROVIDER_ERROR
    assert exc.value.provider == "fake"


def test_gateway_stream_deltas_then_done(gateway):
    events = list(gateway.stream(None, make_request()))
    kinds = [event.kind for event in events]
    assert kinds.count("delta") >= 1
    assert kinds[-1] == "done"
    done = events[-1]
    assert done.usage is not None
    assert done.run_id is None


def test_gateway_stream_rejects_non_streaming_provider(settings):
    registry = ProviderRegistry(settings)
    registry.register(NonStreamingProvider(settings))
    gateway = AIGateway(registry)
    with pytest.raises(AIProviderError) as exc:
        list(gateway.stream(None, make_request(provider="fake")))
    assert exc.value.code == ErrorCode.STREAMING_NOT_SUPPORTED


def test_gateway_stream_resolves_default_provider_and_model(gateway):
    events = list(gateway.stream(None, make_request(provider="", model="")))
    assert events  # sanity: stream produced events
    deltas = [event for event in events if event.kind == "delta"]
    done = events[-1]
    for event in deltas:
        assert event.provider == "fake"
        assert event.model == "fake-model-1"
    assert done.provider == "fake"
    assert done.model == "fake-model-1"


def test_gateway_stream_done_includes_latency(gateway):
    events = list(gateway.stream(None, make_request()))
    done = events[-1]
    assert done.latency_ms is not None
    assert done.latency_ms >= 0


def test_gateway_stream_unexpected_exception_normalized(settings):
    registry = ProviderRegistry(settings)
    registry.register(ExplodingProvider(settings))
    gateway = AIGateway(registry)
    with pytest.raises(AIGatewayError) as exc:
        list(gateway.stream(None, make_request(provider="fake")))
    assert exc.value.code == ErrorCode.INTERNAL_ERROR
    assert "unexpected-internal-detail" not in exc.value.message
    assert "RuntimeError" not in exc.value.message
    assert exc.value.message == "The AI request failed unexpectedly. Please try again."


def test_gateway_failure_record_error_does_not_mask_provider_error(settings, monkeypatch):
    import app.services.prompt_runs as prompt_runs

    registry = ProviderRegistry(settings)
    registry.register(FailingProvider(settings))
    gateway = AIGateway(registry)

    def boom(*args, **kwargs):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(prompt_runs, "record_failure", boom)

    class FakeDB:
        def __init__(self):
            self.rolled_back = False

        def rollback(self):
            self.rolled_back = True

    db = FakeDB()
    request = make_request(
        provider="fake",
        metadata={"prompt_id": str(uuid.uuid4())},
    )
    with pytest.raises(AIProviderError) as exc:
        gateway.generate(db, request)
    assert exc.value.code == ErrorCode.PROVIDER_ERROR
    assert db.rolled_back is True
    assert isinstance(gateway._failure_record_exc, RuntimeError)


def test_gateway_stream_unexpected_keeps_failure_observable_without_db(gateway, settings, monkeypatch):
    # Unexpected stream failures with a prompt linked must also be recorded losslessly.
    import app.services.prompt_runs as prompt_runs

    registry = ProviderRegistry(settings)
    registry.register(ExplodingProvider(settings))
    gateway = AIGateway(registry)

    calls = []

    def record(db, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(prompt_runs, "record_failure", record)

    class FakeDB:
        def rollback(self):
            pass

    request = make_request(
        provider="fake",
        metadata={"prompt_id": str(uuid.uuid4())},
    )
    with pytest.raises(AIGatewayError):
        list(gateway.stream(FakeDB(), request))
    assert len(calls) == 1
    assert calls[0]["error"] == "Provider failure while generating."


def test_gateway_stream_done_serializes_prompt_and_run_ids(gateway, monkeypatch):
    # Exercises the done event carrying resolved metadata end to end.
    import app.services.prompt_runs as prompt_runs

    run_id = uuid.uuid4()

    class DummyRun:
        id = run_id

    def record(db, **kwargs):
        return DummyRun()

    monkeypatch.setattr(prompt_runs, "record_success", record)

    class FakeDB:
        def rollback(self):
            pass

    events = list(
        gateway.stream(
            FakeDB(),
            make_request(
                provider="",
                model="",
                metadata={"prompt_id": str(uuid.uuid4())},
            ),
        )
    )
    done = events[-1]
    assert done.run_id == run_id
    assert done.provider == "fake"
    assert done.model == "fake-model-1"