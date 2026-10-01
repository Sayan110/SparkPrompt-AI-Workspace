"""Normalized prompt-intelligence contracts.

Pydantic models shared by the intelligence service and the ``/api/intelligence/*``
routes. Phase 3A defined the shapes; Phase 3B shipped ``PromptAnalysis`` (analyze) end
to end; Phase 3C finalizes the enhance/create shapes to the structured output contract:
``PromptEnhancement`` (original + improved prompt + bounded improvement list) and
``PromptCreation`` (drafted prompt + rationale). Field bounds keep model output from
growing without limit, and every field is re-validated by the strict response parser.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

MAX_IMPROVEMENTS = 12
IMPROVEMENT_MAX_LENGTH = 500
CREATION_RATIONALE_MAX_LENGTH = 2_000


class PromptAnalysis(BaseModel):
    """Structured review of an existing prompt."""

    clarity: str | None = Field(default=None, description="How understandable the instruction is.")
    specificity: str | None = Field(default=None, description="How precise the desired output is.")
    context: str | None = Field(default=None, description="Whether background and audience are provided.")
    constraints: str | None = Field(default=None, description="Whether limits, tone, and format rules are stated.")
    output_format: str | None = Field(default=None, description="Whether the requested response shape is defined.")
    missing_information: list[str] = Field(
        default_factory=list, description="Information the prompt should include."
    )
    suggestions: list[str] = Field(
        default_factory=list, description="Concrete suggestions for improving the prompt."
    )


class PromptEnhancement(BaseModel):
    """Improved prompt plus a bounded record of what changed (Phase 3C output contract).

    ``extra="forbid"`` enforces the strict output contract: a model response carrying
    additional keys fails validation instead of being silently accepted.
    """

    model_config = ConfigDict(extra="forbid")

    original_prompt: str = Field(
        min_length=1,
        max_length=20_000,
        description="The prompt as submitted, restated by the model.",
    )
    enhanced_prompt: str = Field(
        min_length=1,
        max_length=20_000,
        description="The rewritten prompt.",
    )
    improvements: list[Annotated[str, Field(min_length=1, max_length=IMPROVEMENT_MAX_LENGTH)]] = Field(
        default_factory=list,
        max_length=MAX_IMPROVEMENTS,
        description="Concise descriptions of what changed and why.",
    )


class PromptCreation(BaseModel):
    """Reusable prompt drafted from a goal (Phase 3C output contract).

    ``extra="forbid"`` enforces the strict output contract: a model response carrying
    additional keys fails validation instead of being silently accepted.
    """

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(
        min_length=1,
        max_length=20_000,
        description="The drafted, complete, reusable prompt.",
    )
    rationale: str = Field(
        min_length=1,
        max_length=CREATION_RATIONALE_MAX_LENGTH,
        description="Brief explanation of the key design choices.",
    )