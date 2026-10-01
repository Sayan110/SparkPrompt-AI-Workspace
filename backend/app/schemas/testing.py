from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.testing.service import MAX_INPUT_LENGTH, MAX_PROMPT_LENGTH


class PromptTestRequest(BaseModel):
    """Request to execute a prompt against the AI gateway (Phase 3D).

    Only fields genuinely supported by the gateway are exposed. ``input`` is an
    optional labeled test input appended after the prompt; a blank input is omitted by
    the service. ``prompt_id`` reuses the gateway's PromptRun mechanism: when present,
    the execution is recorded against the saved prompt. The size limits are imported
    from the service so schema and service can never drift apart.
    """

    prompt: str = Field(
        min_length=1,
        max_length=MAX_PROMPT_LENGTH,
        description="The prompt to execute exactly as written.",
    )
    input: str | None = Field(
        default=None,
        max_length=MAX_INPUT_LENGTH,
        description="Optional test input appended as labeled input after the prompt.",
    )
    provider: str | None = Field(
        default=None, max_length=40, description="Provider id; defaults to the server default."
    )
    model: str | None = Field(
        default=None, max_length=120, description="Model id; defaults to the provider default."
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1, le=262_144)
    prompt_id: UUID | None = Field(
        default=None,
        description="Optional saved-prompt id; when present the run is persisted as a PromptRun.",
    )
    version_id: UUID | None = Field(
        default=None,
        description="Optional saved-version id; requires prompt_id. The server "
        "ownership-resolves the version and executes its stored body instead of "
        "the supplied prompt text, persisting the run with that version identity.",
    )

    @field_validator("prompt")
    @classmethod
    def _prompt_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value

    @model_validator(mode="after")
    def _version_needs_prompt(self) -> "PromptTestRequest":
        if self.version_id is not None and self.prompt_id is None:
            raise ValueError("version_id requires prompt_id.")
        return self