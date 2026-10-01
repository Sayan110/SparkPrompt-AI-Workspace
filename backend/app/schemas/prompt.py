from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PromptBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    idea: str = Field(min_length=1)
    audience: str = Field(default="everyone", max_length=40)
    output_format: str = Field(default="best", max_length=40)
    depth: int = Field(default=2, ge=1, le=3)
    project_id: UUID | None = None


class PromptCreate(PromptBase):
    body: str | None = Field(default=None, description="Generated prompt text to store as the initial version.")


class PromptUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    idea: str | None = Field(default=None, min_length=1)
    audience: str | None = Field(default=None, max_length=40)
    output_format: str | None = Field(default=None, max_length=40)
    depth: int | None = Field(default=None, ge=1, le=3)
    project_id: UUID | None = None
    body: str | None = Field(
        default=None,
        description=(
            "New prompt text. When supplied and non-empty, a NEW prompt version is "
            "created and all previous versions are preserved. Omit (or send an empty "
            "value) to update metadata only, exactly as before Phase 3I. The stored "
            "body is never overwritten in place."
        ),
    )


class PromptRead(PromptBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
    body: str | None = Field(
        default=None,
        description="Latest prompt version body, when a version exists.",
    )
    version_number: int | None = Field(
        default=None,
        description=(
            "Number of the version whose body is returned above, or null when the "
            "prompt has no version. Server truth, so a client never has to infer the "
            "version from how many times it saved."
        ),
    )

    model_config = {"from_attributes": True}


class PromptVersionRead(BaseModel):
    """One immutable stored version (Phase 3J). Exactly the fields a history UI needs.

    ``prompt_id`` is deliberately omitted: this schema is only ever returned from
    prompt-scoped endpoints, so the owning prompt is already known. Nothing here is
    writable — there is no API that changes any of these columns on an existing row.
    """

    id: UUID
    version_number: int
    body: str
    created_at: datetime

    model_config = {"from_attributes": True}