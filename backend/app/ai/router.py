from __future__ import annotations

from app.ai.errors import AIGatewayError, ErrorCode
from app.ai.registry import ProviderRegistry

from app.ai.providers.base import BaseProvider


class ModelRouter:
    """Resolves (provider, model) names to a provider adapter. No provider API logic here."""

    def __init__(self, registry: ProviderRegistry):
        self._registry = registry

    def resolve_provider(self, provider_id: str | None) -> BaseProvider:
        requested = provider_id or self._registry.default_provider_id()
        provider = self._registry.get(requested)
        if provider is None:
            raise AIGatewayError(
                ErrorCode.UNKNOWN_PROVIDER,
                f"Unknown AI provider '{requested}'.",
                provider=requested,
            )
        return provider

    def resolve_model(self, provider: BaseProvider, model: str | None) -> str:
        if model:
            return model
        default = provider.default_model()
        if not default:
            raise AIGatewayError(
                ErrorCode.INVALID_REQUEST,
                f"Provider '{provider.id}' has no default model and no model was provided.",
                provider=provider.id,
            )
        return default

    def resolve(self, provider_id: str | None, model: str | None) -> tuple[BaseProvider, str]:
        provider = self.resolve_provider(provider_id)
        return provider, self.resolve_model(provider, model)