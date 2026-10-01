from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


def _not_blank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


class IntelligenceAnalyzeRequest(BaseModel):
    prompt: str = Field(
        min_length=1,
        max_length=20_000,
        description="The prompt to analyze.",
    )
    provider: str | None = Field(
        default=None, max_length=40, description="Provider id; defaults to the server default."
    )
    model: str | None = Field(
        default=None, max_length=120, description="Model id; defaults to the provider default."
    )

    @field_validator("prompt")
    @classmethod
    def _prompt_not_blank(cls, value: str) -> str:
        return _not_blank(value, "prompt")


class IntelligenceEnhanceRequest(BaseModel):
    prompt: str = Field(
        min_length=1,
        max_length=20_000,
        description="The prompt to improve while preserving its intent.",
    )
    provider: str | None = Field(
        default=None, max_length=40, description="Provider id; defaults to the server default."
    )
    model: str | None = Field(
        default=None, max_length=120, description="Model id; defaults to the provider default."
    )

    @field_validator("prompt")
    @classmethod
    def _prompt_not_blank(cls, value: str) -> str:
        return _not_blank(value, "prompt")


class IntelligenceCreateRequest(BaseModel):
    goal: str = Field(
        min_length=1,
        max_length=20_000,
        description="The goal the generated prompt should achieve.",
    )
    context: str | None = Field(
        default=None,
        max_length=20_000,
        description="Optional background, audience, platform, or constraints.",
    )
    provider: str | None = Field(
        default=None, max_length=40, description="Provider id; defaults to the server default."
    )
    model: str | None = Field(
        default=None, max_length=120, description="Model id; defaults to the provider default."
    )

    @field_validator("goal")
    @classmethod
    def _goal_not_blank(cls, value: str) -> str:
        return _not_blank(value, "goal")