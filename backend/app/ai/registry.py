from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.providers import GeminiProvider, NvidiaProvider, OllamaProvider
from app.ai.types import ProviderInfo
from app.core.config import Settings, get_settings

if TYPE_CHECKING:
    from app.ai.providers.base import BaseProvider


class ProviderRegistry:
    """Holds the configured provider adapters and answers availability questions."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._providers: dict[str, BaseProvider] = {}

    def register(self, provider: BaseProvider) -> None:
        self._providers[provider.id] = provider

    def get(self, provider_id: str) -> BaseProvider | None:
        return self._providers.get(provider_id)

    def all(self) -> list[BaseProvider]:
        return list(self._providers.values())

    def ids(self) -> list[str]:
        return sorted(self._providers.keys())

    def info(self, provider: BaseProvider) -> ProviderInfo:
        return ProviderInfo(
            id=provider.id,
            name=provider.name,
            available=provider.is_configured() and provider.is_available(),
            configured=provider.is_configured(),
            models=provider.models(),
            default_model=provider.default_model(),
            capabilities=provider.capabilities(),
        )

    def infos(self) -> list[ProviderInfo]:
        return [self.info(provider) for provider in self.all()]

    def is_available(self, provider_id: str) -> bool:
        provider = self.get(provider_id)
        return bool(provider and provider.is_available())

    def default_provider_id(self) -> str:
        return self._settings.ai_default_provider or "gemini"


def build_registry(settings: Settings | None = None) -> ProviderRegistry:
    settings = settings or get_settings()
    registry = ProviderRegistry(settings)
    registry.register(GeminiProvider(settings))
    registry.register(NvidiaProvider(settings))
    registry.register(OllamaProvider(settings))
    return registry