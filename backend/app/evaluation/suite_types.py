"""Evaluation suite domain contracts (Phase 3G).

A suite applies ONE ``EvaluatorConfig`` to MULTIPLE targets — existing owned
PromptRuns (MODE A) and/or fresh ``EvaluationExecution`` definitions (MODE B) —
and returns integer counts (``total`` targets evaluated, how many ``passed``)
plus each per-target :class:`EvaluationResult` unchanged, in the request's order.

Deliberately minimal: the only aggregation is counting. No ratios,
percentages, rankings, embeddings, persistence, or LLM-as-judge ever appear in
suite output — the Phase 3E result contract is preserved per target (including
its derived per-result score).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.evaluation.types import EvaluationExecution, EvaluationResult

#: Hard ceiling on suite size (inclusive). Mirrors the Phase 3L target cap style:
#: bounded, documented, and enforced at BOTH the wire schema (422) and the
#: service layer (400) so direct callers get the same contract as the API.
MAX_SUITE_TARGETS = 20


class EvaluationSuiteTarget(BaseModel):
    """One suite target: an existing run OR a fresh execution (exactly one).

    Mirrors the Phase 3E ``EvaluationRequest`` XOR contract per target: a target
    with neither ``run_id`` nor ``execution``, or with both, is invalid.
    """

    run_id: UUID | None = None
    execution: EvaluationExecution | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "EvaluationSuiteTarget":
        if (self.run_id is None) == (self.execution is None):
            raise ValueError("Provide exactly one of run_id or execution.")
        return self


class EvaluationSuiteResult(BaseModel):
    """Aggregate outcome of one suite run: integer counts plus per-target results.

    ``total``/``passed`` are plain integers — never scores, ratios, percentages,
    or rankings. The per-target ``evaluations`` preserve the request's order.
    """

    total: int = Field(description="Number of targets evaluated.")
    passed: int = Field(description="How many targets passed every criterion.")
    evaluations: list[EvaluationResult] = Field(
        description="Per-target results, preserving the request order."
    )
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the suite was run.",
    )