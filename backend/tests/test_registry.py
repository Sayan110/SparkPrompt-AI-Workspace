from app.ai.registry import ProviderRegistry
from app.ai.types import ProviderInfo
from app.core.config import Settings

from conftest import FakeProvider


def test_registry_registers_and_retrieves(settings: Settings):
    registry = ProviderRegistry(settings)
    provider = FakeProvider(settings)
    registry.register(provider)
    assert registry.get("fake") is provider
    assert registry.get("missing") is None
    assert registry.ids() == ["fake"]


def test_registry_info_surface(settings: Settings):
    registry = ProviderRegistry(settings)
    registry.register(FakeProvider(settings))
    info = registry.infos()
    assert len(info) == 1
    item: ProviderInfo = info[0]
    assert item.id == "fake"
    assert item.available is True
    assert item.configured is True
    assert "fake-model-1" in item.models
    assert item.default_model == "fake-model-1"
    assert item.capabilities.streaming is True


def test_is_available_only_for_known_providers(settings: Settings):
    registry = ProviderRegistry(settings)
    registry.register(FakeProvider(settings))
    assert registry.is_available("fake") is True
    assert registry.is_available("missing") is False


def test_build_registry_marks_remote_providers_unconfigured():
    registry = ProviderRegistry(
        Settings(
            gemini_api_key="",
            nvidia_api_key="",
            ollama_base_url="http://127.0.0.1:1",
            ai_timeout_seconds=0.5,
        )
    )
    from app.ai.providers import GeminiProvider, NvidiaProvider, OllamaProvider

    registry.register(GeminiProvider(registry._settings))
    registry.register(NvidiaProvider(registry._settings))
    registry.register(OllamaProvider(registry._settings))

    infos = {info.id: info for info in registry.infos()}
    assert infos["gemini"].configured is False
    assert infos["gemini"].available is False
    assert infos["nvidia"].configured is False
    assert infos["nvidia"].available is False
    # Ollama: configured by default, but unreachable -> unavailable (no crash).
    assert infos["ollama"].configured is True
    assert infos["ollama"].available is False