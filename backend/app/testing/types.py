"""Normalized prompt-testing contracts (Phase 3D).

``PromptTestResult`` is the execution result of running a prompt through the existing
AI gateway: the raw model output plus the execution metadata the Studio displays.
It deliberately reuses the field shapes and naming conventions of the gateway's
``AIResponse``/``AIUsage`` (see :mod:`app.ai.types`) instead of duplicating the whole
model — this contract only carries what the API client and UI need.

Phase 3D is execution only: there is no scoring, grading, or evaluation in this
contract. ``usage`` is ``None`` when the provider did not report token usage, matching
the project convention (never fabricate usage).
"""

from uuid import UUID

from pydantic import BaseModel, Field


class PromptTestUsage(BaseModel):
    """Normalized token usage for a test execution. None means the provider did not report it."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class PromptTestResult(BaseModel):
    """The normalized outcome of executing a prompt against the AI gateway."""

    output: str = Field(description="The raw model output for the tested prompt.")
    provider: str = Field(description="The provider that executed the request.")
    model: str = Field(description="The model that executed the request.")
    finish_reason: str | None = Field(
        default=None, description="Why generation stopped (e.g. 'stop')."
    )
    latency_ms: int | None = Field(default=None, description="Round-trip latency in milliseconds.")
    usage: PromptTestUsage | None = Field(
        default=None, description="Token usage; null when the provider did not report it."
    )
    request_id: UUID = Field(description="Correlates this execution with the gateway request.")
    prompt_id: UUID | None = Field(
        default=None,
        description="Present only when the test was attached to a saved prompt (PromptRun).",
    )
    run_id: UUID | None = Field(
        default=None,
        description="PromptRun id; present only when the test was attached to a saved prompt.",
    )
    version_id: UUID | None = Field(
        default=None,
        description="Exact PromptVersion executed; present only for version-scoped "
        "executions. NULL means draft/arbitrary or pre-3O execution.",
    )