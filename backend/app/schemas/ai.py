from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

AiRole = Literal["system", "user", "assistant"]


class AiMessage(BaseModel):
    role: AiRole
    content: str = Field(min_length=1, max_length=200_000)

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class AiGenerateRequest(BaseModel):
    provider: str | None = Field(
        default=None, max_length=40, description="Provider id; defaults to the server default."
    )
    model: str | None = Field(
        default=None, max_length=120, description="Model id; defaults to the provider default."
    )
    messages: list[AiMessage] = Field(min_length=1, max_length=64)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1, le=262_144)
    stream: bool = Field(default=False)
    metadata: dict[str, str] | None = Field(
        default=None,
        description="App-specific metadata. keys are validated; no keys or credentials stored.",
    )


class AiUsageOut(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class AiGenerateResponse(BaseModel):
    text: str
    provider: str
    model: str
    finish_reason: str | None = None
    usage: AiUsageOut | None = None
    request_id: UUID
    latency_ms: int | None = None
    streamed: bool = False
    created_at: datetime
    prompt_id: UUID | None = None
    run_id: UUID | None = None


class AiProviderInfoOut(BaseModel):
    id: str
    name: str
    available: bool
    configured: bool
    streaming: bool = False
    models: list[str] = Field(default_factory=list)
    default_model: str | None = None


class AiModelsOut(BaseModel):
    provider: str
    models: list[str] = Field(default_factory=list)


class AiStatus(BaseModel):
    status: str
    phase: str
    ai_runtime: str
    providers: dict[str, str]
    message: str