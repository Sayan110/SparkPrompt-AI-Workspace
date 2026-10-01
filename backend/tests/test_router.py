import pytest

from app.ai.errors import AIGatewayError, ErrorCode
from app.ai.router import ModelRouter

from conftest import FakeProvider, make_request


def test_resolve_unknown_provider(registry):
    router = ModelRouter(registry)
    with pytest.raises(AIGatewayError) as exc:
        router.resolve("missing", None)
    assert exc.value.code == ErrorCode.UNKNOWN_PROVIDER


def test_resolve_with_default_provider_and_model(registry):
    provider, model = ModelRouter(registry).resolve(None, None)
    assert provider.id == "fake"
    assert model == "fake-model-1"


def test_resolve_explicit_model(registry):
    _, model = ModelRouter(registry).resolve("fake", "fake-model-2")
    assert model == "fake-model-2"


def test_resolve_missing_default_model_rejected(settings, registry):
    class NoDefault(FakeProvider):
        def default_model(self):
            return None

    registry.register(NoDefault(settings))
    router = ModelRouter(registry)
    with pytest.raises(AIGatewayError) as exc:
        router.resolve("missing-provider", None)
    assert exc.value.code == ErrorCode.UNKNOWN_PROVIDER

    with pytest.raises(AIGatewayError) as exc:
        router.resolve("fake", None)
    assert exc.value.code == ErrorCode.INVALID_REQUEST