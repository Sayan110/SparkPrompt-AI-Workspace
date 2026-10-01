from __future__ import annotations

import json
import time
from typing import Iterator

import httpx

from app.ai.errors import AIProviderError, ErrorCode
from app.ai.providers.base import BaseProvider
from app.ai.types import AIRequest, AIResponse, AIStreamEvent, AIUsage, ProviderCapabilities

_TAGS_CACHE_TTL = 5.0


class OllamaProvider(BaseProvider):
    id = "ollama"
    name = "Ollama (local)"

    def __init__(self, settings):
        super().__init__(settings)
        self._tags_at = 0.0
        self._tags_cache: list[str] = []

    def _base_url(self) -> str:
        return (self._settings.ollama_base_url or "http://127.0.0.1:11434").rstrip("/")

    def is_configured(self) -> bool:
        # Local service: always configured by default; availability reflects reachability.
        return True

    def is_available(self) -> bool:
        try:
            return bool(self._live_tags())
        except AIProviderError:
            return False

    def _live_tags(self) -> list[str]:
        now = time.monotonic()
        if now - self._tags_at < _TAGS_CACHE_TTL:
            return self._tags_cache
        self._tags_at = now
        tags: list[str] = []
        try:
            response = self._get_json(self._client(), self._base_url() + "/api/tags")
            data = self._parse_json(response)
            for model in data.get("models") or []:
                if isinstance(model, dict) and model.get("name"):
                    tags.append(model["name"])
        except AIProviderError:
            tags = []
        self._tags_cache = tags
        return tags

    def models(self) -> list[str]:
        tags = self._live_tags()
        if tags:
            return tags
        default = self.default_model()
        return [default] if default else []

    def default_model(self) -> str | None:
        configured = self._settings.ollama_default_model
        if configured:
            return configured
        tags = self._live_tags()
        return tags[0] if tags else "llama3.2"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True)

    def generate(self, request: AIRequest) -> AIResponse:
        response = self._post_json(
            self._client(),
            self._base_url() + "/api/chat",
            payload=self._build_payload(request, stream=False),
        )
        data = self._parse_json(response)
        text = (data.get("message") or {}).get("content") or ""
        if not text:
            raise AIProviderError(
                self.id, ErrorCode.INVALID_RESPONSE, "Ollama returned an empty completion."
            )
        return AIResponse(
            text=text,
            provider=self.id,
            model=request.model,
            finish_reason=data.get("done_reason"),
            usage=AIUsage(
                prompt_tokens=data.get("prompt_eval_count"),
                completion_tokens=data.get("eval_count"),
                total_tokens=None,
            ),
            request_id=request.request_id,
        )

    def stream(self, request: AIRequest) -> Iterator[AIStreamEvent]:
        with self._client() as client:
            try:
                stream = client.stream(
                    "POST",
                    self._base_url() + "/api/chat",
                    json=self._build_payload(request, stream=True),
                )
            except httpx.TimeoutException:
                raise AIProviderError(self.id, ErrorCode.TIMEOUT, "Ollama request timed out.")
            except httpx.HTTPError:
                raise AIProviderError(self.id, ErrorCode.PROVIDER_ERROR, "Ollama request failed.")
            with stream:
                self._raise_for_status(stream.response)
                for line in stream.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(data, dict):
                        continue
                    text = (data.get("message") or {}).get("content") or ""
                    if text:
                        yield AIStreamEvent(kind="delta", text=text)
                    if data.get("done"):
                        yield AIStreamEvent(
                            kind="done",
                            finish_reason=data.get("done_reason"),
                            usage=AIUsage(
                                prompt_tokens=data.get("prompt_eval_count"),
                                completion_tokens=data.get("eval_count"),
                                total_tokens=None,
                            ),
                        )

    def _build_payload(self, request: AIRequest, stream: bool) -> dict:
        messages = self._chat_messages(request.messages)
        system = self._system_text(request.messages)
        if system:
            messages.insert(0, {"role": "system", "content": system})
        return {
            "model": request.model,
            "messages": messages,
            "stream": stream,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
        }