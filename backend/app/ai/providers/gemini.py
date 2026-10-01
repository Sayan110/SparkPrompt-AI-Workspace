from __future__ import annotations

from typing import Iterator

import httpx

from app.ai.errors import AIProviderError, ErrorCode
from app.ai.providers.base import BaseProvider
from app.ai.types import AIRequest, AIResponse, AIStreamEvent, AIUsage, ProviderCapabilities

_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_ROLE_MAP = {
    "user": "user",
    "assistant": "model",
    "system": "user",
}


class GeminiProvider(BaseProvider):
    id = "gemini"
    name = "Google Gemini"

    BASE_MODELS = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash", "gemini-1.5-pro"]

    def is_configured(self) -> bool:
        return bool(self._settings.gemini_api_key)

    def models(self) -> list[str]:
        return self.BASE_MODELS

    def default_model(self) -> str:
        return self._settings.gemini_default_model or "gemini-2.5-flash"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True)

    def generate(self, request: AIRequest) -> AIResponse:
        response = self._post_json(
            self._client(),
            _GEMINI_ENDPOINT.format(model=request.model),
            headers={"x-goog-api-key": self._settings.gemini_api_key},
            payload=self._build_payload(request),
        )
        data = self._parse_json(response)
        text = self._extract_text(data)
        if not text:
            raise AIProviderError(
                self.id, ErrorCode.INVALID_RESPONSE, "Gemini returned an empty completion."
            )
        candidates = data.get("candidates") or []
        finish_reason = candidates[0].get("finishReason") if candidates else None
        usage = self._extract_usage(data)
        return AIResponse(
            text=text,
            provider=self.id,
            model=request.model,
            finish_reason=finish_reason,
            usage=usage,
            request_id=request.request_id,
        )

    def stream(self, request: AIRequest) -> Iterator[AIStreamEvent]:
        with self._client() as client:
            try:
                stream = client.stream(
                    "POST",
                    _GEMINI_ENDPOINT.format(model=request.model),
                    headers={"x-goog-api-key": self._settings.gemini_api_key},
                    params={"alt": "sse"},
                    json=self._build_payload(request),
                )
            except httpx.TimeoutException:
                raise AIProviderError(self.id, ErrorCode.TIMEOUT, "Gemini request timed out.")
            except httpx.HTTPError:
                raise AIProviderError(self.id, ErrorCode.PROVIDER_ERROR, "Gemini request failed.")
            with stream:
                self._raise_for_status(stream.response)
                for line in stream.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    try:
                        data = _json_line(line)
                    except ValueError:
                        continue
                    usage = self._extract_usage(data)
                    if usage:
                        yield AIStreamEvent(kind="done", usage=usage)
                    text = self._extract_text(data)
                    if text:
                        yield AIStreamEvent(kind="delta", text=text)

    def _build_payload(self, request: AIRequest) -> dict:
        system = self._system_text(request.messages)
        payload: dict = {
            "contents": [
                {"role": _ROLE_MAP.get(message.role, "user"), "parts": [{"text": message.content}]}
                for message in request.messages
                if message.role != "system"
            ],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        return payload

    @staticmethod
    def _extract_text(data: dict) -> str:
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = (candidates[0].get("content") or {}).get("parts") or []
        chunks = [part.get("text", "") for part in parts if isinstance(part, dict)]
        return "".join(chunks)

    @staticmethod
    def _extract_usage(data: dict) -> AIUsage | None:
        metadata = data.get("usageMetadata")
        if not isinstance(metadata, dict):
            return None
        return AIUsage(
            prompt_tokens=metadata.get("promptTokenCount"),
            completion_tokens=metadata.get("candidatesTokenCount"),
            total_tokens=metadata.get("totalTokenCount"),
        )


def _json_line(line: str) -> dict:
    payload = line[len("data: ") :].strip()
    if not payload:
        raise ValueError("empty data line")
    return _parse_json_payload(payload)


def _parse_json_payload(payload: str) -> dict:
    import json

    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("expected object")
    return data