from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

import httpx

from app.ai.errors import AIProviderError, ErrorCode
from app.ai.types import AIRequest, AIResponse, AIStreamEvent, AIUsage, ProviderCapabilities
from app.core.config import Settings


class BaseProvider(ABC):
    """Provider-agnostic contract. Adapters never leak provider-specific payloads."""

    id: str = ""
    name: str = ""

    def __init__(self, settings: Settings):
        self._settings = settings

    @property
    def _timeout(self) -> float:
        return self._settings.ai_timeout_seconds

    def is_configured(self) -> bool:
        raise NotImplementedError

    def is_available(self) -> bool:
        """Whether the provider can actually serve requests right now."""
        return self.is_configured()

    def models(self) -> list[str]:
        return []

    def default_model(self) -> str | None:
        return None

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=False)

    @abstractmethod
    def generate(self, request: AIRequest) -> AIResponse:
        """Run a single non-streaming generation and return a normalized response."""

    def stream(self, request: AIRequest) -> Iterator[AIStreamEvent]:
        raise AIProviderError(
            self.id,
            ErrorCode.STREAMING_NOT_SUPPORTED,
            f"{self.name} does not support streaming in this phase.",
        )

    # -- shared HTTP helpers -------------------------------------------------

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self._timeout)

    def _post_json(
        self,
        client: httpx.Client,
        url: str,
        payload: dict,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        try:
            response = client.post(url, json=payload, headers=headers, params=params)
        except httpx.TimeoutException:
            raise AIProviderError(self.id, ErrorCode.TIMEOUT, f"{self.name} request timed out.")
        except httpx.ConnectError:
            raise AIProviderError(
                self.id, ErrorCode.PROVIDER_UNAVAILABLE, f"{self.name} is unreachable."
            )
        except httpx.HTTPError:
            raise AIProviderError(self.id, ErrorCode.PROVIDER_ERROR, f"{self.name} request failed.")
        self._raise_for_status(response)
        return response

    def _get_json(
        self,
        client: httpx.Client,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        try:
            response = client.get(url, headers=headers)
        except httpx.TimeoutException:
            raise AIProviderError(self.id, ErrorCode.TIMEOUT, f"{self.name} request timed out.")
        except httpx.ConnectError:
            raise AIProviderError(
                self.id, ErrorCode.PROVIDER_UNAVAILABLE, f"{self.name} is unreachable."
            )
        except httpx.HTTPError:
            raise AIProviderError(self.id, ErrorCode.PROVIDER_ERROR, f"{self.name} request failed.")
        self._raise_for_status(response)
        return response

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        mapping: dict[int, ErrorCode] = {
            400: ErrorCode.INVALID_REQUEST,
            401: ErrorCode.AUTHENTICATION_ERROR,
            403: ErrorCode.AUTHENTICATION_ERROR,
            404: ErrorCode.INVALID_MODEL,
            408: ErrorCode.TIMEOUT,
            410: ErrorCode.INVALID_MODEL,
            429: ErrorCode.RATE_LIMIT,
        }
        code = mapping.get(response.status_code, ErrorCode.PROVIDER_ERROR)
        if 500 <= response.status_code < 600:
            code = ErrorCode.PROVIDER_ERROR
        raise AIProviderError(self.id, code, _stable_error_message(self.name, code, response.status_code))

    def _parse_json(self, response: httpx.Response) -> dict:
        try:
            data = response.json()
        except ValueError:
            raise AIProviderError(
                self.id, ErrorCode.INVALID_RESPONSE, f"{self.name} returned a malformed response."
            )
        if not isinstance(data, dict):
            raise AIProviderError(
                self.id, ErrorCode.INVALID_RESPONSE, f"{self.name} returned a malformed response."
            )
        return data

    @staticmethod
    def _system_text(messages: list) -> str:
        return "".join(message.content for message in messages if message.role == "system")

    @staticmethod
    def _chat_messages(messages: list) -> list[dict]:
        return [
            {"role": message.role, "content": message.content}
            for message in messages
            if message.role != "system"
        ]


_ERROR_MESSAGE: dict[ErrorCode, str] = {
    ErrorCode.INVALID_REQUEST: "{name} rejected the request",
    ErrorCode.AUTHENTICATION_ERROR: "{name} authentication failed",
    ErrorCode.INVALID_MODEL: "{name} reported an invalid model",
    ErrorCode.TIMEOUT: "{name} request timed out",
    ErrorCode.RATE_LIMIT: "{name} rate limited the request",
    ErrorCode.PROVIDER_ERROR: "{name} request failed",
}


def _stable_error_message(name: str, code: ErrorCode, status: int) -> str:
    template = _ERROR_MESSAGE.get(code, "{name} request failed")
    return f"{template.format(name=name)} (status {status})."