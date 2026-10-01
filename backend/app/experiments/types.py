"""Prompt experiment domain contracts (Phase 3H).

An experiment applies ONE ``EvaluatorConfig`` to EVERY persisted ``PromptVersion`` of a
single owned prompt, and reports integer counts (``total_versions`` measured,
``passed``) plus each per-version :class:`~app.evaluation.types.EvaluationResult`
unchanged, ordered by ascending ``version_number``.

Deliberately minimal, exactly like the Phase 3G suite contract: the only aggregation is
counting. The per-version payload is the EXISTING Phase 3E ``EvaluationResult`` — there
is no second evaluation structure, and no percentage aggregate, ratio, grade, ranking,
winner, recommendation, or pass-rate ever appears in experiment output. (Each
per-version result carries its own derived score as descriptive metadata.) An experiment
is stateless: nothing is stored, no version is modified, and no history is kept.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.evaluation.types import EvaluationResult

#: Hard ceiling on how many versions one experiment may evaluate (inclusive). Matches
#: the Phase 3G ``MAX_SUITE_TARGETS`` precedent: bounded, documented, and enforced in the
#: service so an oversized prompt is rejected cleanly instead of being silently
#: truncated or silently reduced to "the most recent N versions".
MAX_EXPERIMENT_VERSIONS = 20


class ExperimentResult(BaseModel):
    """Aggregate outcome of one experiment: integer counts plus per-version results.

    ``total_versions``/``passed`` are plain integers — never scores, ratios,
    percentages, grades, or rankings. The ``evaluations`` list is ordered by ascending
    ``version_number`` and preserves the Phase 3E ``EvaluationResult`` contract per
    version. A prompt with no persisted versions yields ``total_versions=0``,
    ``passed=0``, and an empty list rather than an error: "no versions yet" is a factual
    measurement, not a malformed request.
    """

    total_versions: int = Field(
        description="Number of persisted prompt versions evaluated."
    )
    passed: int = Field(
        description="How many versions passed every criterion of the evaluator."
    )
    evaluations: list[EvaluationResult] = Field(
        description="Per-version results, ordered by ascending version_number."
    )
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the experiment was run.",
    )
