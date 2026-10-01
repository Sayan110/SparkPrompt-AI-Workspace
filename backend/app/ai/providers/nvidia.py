from __future__ import annotations

import json
from typing import Iterator

import httpx

from app.ai.errors import AIProviderError, ErrorCode
from app.ai.providers.base import BaseProvider
from app.ai.types import AIRequest, AIResponse, AIStreamEvent, AIUsage, ProviderCapabilities


class NvidiaProvider(BaseProvider):
    id = "nvidia"
    name = "NVIDIA"

    BASE_MODELS = [
        "nvidia/llama-3.3-nemotron-70b-instruct",
        "meta/llama-3.1-405b-instruct",
        "deepseek-ai/deepseek-r1",
    ]

    def is_configured(self) -> bool:
        return bool(self._settings.nvidia_api_key)

    def models(self) -> list[str]:
        return self.BASE_MODELS

    def default_model(self) -> str:
        return self._settings.nvidia_default_model or "nvidia/llama-3.3-nemotron-70b-instruct"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True)

    def _endpoint(self) -> str:
        base = self._settings.nvidia_base_url or "https://integration.api.nvidia.com"
        return base.rstrip("/") + "/v1/chat/completions"

    def generate(self, request: AIRequest) -> AIResponse:
        response = self._post_json(
            self._client(),
            self._endpoint(),
            headers={"Authorization": f"Bearer {self._settings.nvidia_api_key}"},
            payload=self._build_payload(request, stream=False),
        )
        data = self._parse_json(response)
        text = self._extract_text(data)
        if not text:
            raise AIProviderError(
                self.id, ErrorCode.INVALID_RESPONSE, "NVIDIA returned an empty completion."
            )
        choices = data.get("choices") or []
        finish_reason = choices[0].get("finish_reason") if choices else None
        return AIResponse(
            text=text,
            provider=self.id,
            model=request.model,
            finish_reason=finish_reason,
            usage=self._extract_usage(data),
            request_id=request.request_id,
        )

    def stream(self, request: AIRequest) -> Iterator[AIStreamEvent]:
        with self._client() as client:
            try:
                stream = client.stream(
                    "POST",
                    self._endpoint(),
                    headers={"Authorization": f"Bearer {self._settings.nvidia_api_key}"},
                    json=self._build_payload(request, stream=True),
                )
            except httpx.TimeoutException:
                raise AIProviderError(self.id, ErrorCode.TIMEOUT, "NVIDIA request timed out.")
            except httpx.HTTPError:
                raise AIProviderError(self.id, ErrorCode.PROVIDER_ERROR, "NVIDIA request failed.")
            with stream:
                self._raise_for_status(stream.response)
                finish_reason: str | None = None
                for line in stream.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[len("data: ") :].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                    except ValueError:
                        continue
                    if not isinstance(data, dict):
                        continue
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content") or ""
                    if text:
                        yield AIStreamEvent(kind="delta", text=text)
                    if choices[0].get("finish_reason"):
                        finish_reason = choices[0].get("finish_reason")
                yield AIStreamEvent(kind="done", finish_reason=finish_reason, usage=None)

    def _build_payload(self, request: AIRequest, stream: bool) -> dict:
        system = self._system_text(request.messages)
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.extend(
            {"role": message.role, "content": message.content}
            for message in request.messages
            if message.role != "system"
        )
        return {
            "model": request.model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": stream,
        }

    @staticmethod
    def _extract_text(data: dict) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return message.get("content") or ""

    @staticmethod
    def _extract_usage(data: dict) -> AIUsage | None:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return None
        return AIUsage(
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )