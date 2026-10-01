from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

AiRole = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class AIMessage:
    role: AiRole
    content: str


@dataclass(frozen=True)
class AIUsage:
    """Normalized token usage. None means the provider did not report it."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    def to_snapshot(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True)
class AIRequest:
    provider: str
    model: str
    messages: list[AIMessage]
    temperature: float = 0.7
    max_tokens: int = 2048
    stream: bool = False
    metadata: dict[str, str] | None = None
    request_id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass(frozen=True)
class AIResponse:
    text: str
    provider: str
    model: str
    finish_reason: str | None
    usage: AIUsage | None
    request_id: uuid.UUID
    latency_ms: int | None = None
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class AIStreamEvent:
    """Normalized streaming event. kinds: delta | done | error"""

    kind: str
    text: str = ""
    finish_reason: str | None = None
    usage: AIUsage | None = None
    message: str = ""
    provider: str = ""
    model: str = ""
    latency_ms: int | None = None
    prompt_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    streaming: bool = False


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    name: str
    available: bool
    configured: bool
    models: list[str]
    default_model: str | None
    capabilities: ProviderCapabilities