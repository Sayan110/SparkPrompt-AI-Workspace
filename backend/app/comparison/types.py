"""Normalized prompt-comparison contracts (Phase 3F).

Phase 3F compares two already-produced evaluation results — the SAME
:class:`~app.evaluation.types.EvaluationResult` contract produced by Phase 3E —
and describes factual differences between them. The vocabulary is deliberately
neutral: no winner/loser, no ranking, no recommendation. Per-side deterministic
scores (Phase 3K) are carried as factual metadata only — never as a verdict. A
comparison
either reports structural criterion differences term-by-term (``criterion_diffs``)
or declares the evaluators non-comparable and reports what differs between them
(``evaluator_compatibility.mismatches``), while output / prompt / metadata
differences are always reported.

Like Phase 3E, every contract here is AI-free: no provider, gateway, router, or
registry import. The module reuses ``app.evaluation.types`` models
(``EvaluationResult``, ``EvaluatorConfig``, ``RuleType``, ``EvaluationUsage``),
which are themselves AI-free.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.evaluation.types import EvaluationResult, EvaluationUsage, RuleType

MAX_DIFF_CHARS = 20_000
"""Upper bound for each side fed into the line diff (bounded comparison)."""
MAX_DIFF_HUNKS = 40
"""Upper bound for the number of diff entries returned in the response."""
MAX_DIFF_LINE_LENGTH = 200
"""Upper bound for a single diff line shown in the response."""
MAX_OUTPUT_DISPLAY = 10_000
"""Upper bound for the output text exposed per side in an execution summary."""
MAX_SUPPLIED_OUTPUT = 100_000
"""Upper bound for a client-supplied output in supplied (MODE B) comparison."""


class FieldMismatch(BaseModel):
    """One concrete difference between two evaluator snapshots.

    ``rule_index`` is the positional rule index (0-based) where the difference
    was found, or ``-1`` for whole-evaluator differences (``rule_count`` /
    ``expected_output``).
    """

    rule_index: int = Field(..., description="Position of the differing rule, or -1 for whole-evaluator fields.")
    field: str = Field(..., description="Name of the differing field.")
    left_value: str | int | bool | None = Field(..., description="Value in the left evaluator.")
    right_value: str | int | bool | None = Field(..., description="Value in the right evaluator.")


class EvaluatorCompatibility(BaseModel):
    """Whether two evaluator snapshots can be compared criterion-by-criterion.

    Snapshot comparison is positional and structural: same rule count, same type
    and same semantic parameters at each position, same ``expected_output`` at the
    evaluator level. Names, rule ids, and labels are cosmetic and ignored.
    """

    comparable: bool = Field(..., description="True when criterion-by-criterion comparison is possible.")
    status: Literal["identical", "incompatible"] = Field(..., description="Structural comparison outcome.")
    mismatches: list[FieldMismatch] = Field(default_factory=list, description="Concrete snapshot differences.")


class CriterionSide(BaseModel):
    """One side of a criterion comparison (passed state + bounded evidence)."""

    passed: bool
    evidence: dict


class CriterionLabel(BaseModel):
    """The (cosmetic) label each side attached to the same positional rule."""

    left: str | None
    right: str | None


class CriterionDiff(BaseModel):
    """Factual difference for one positional criterion between two results.

    ``state`` uses neutral vocabulary (``same_pass`` / ``same_fail`` /
    ``left_only_pass`` / ``right_only_pass``) — never winner/loser/better/worse.
    ``evidence_delta`` carries only numeric evidence fields that differ between
    the two sides (e.g. ``{"actual_length": {"left": 44, "right": 61}}``); it
    never interprets quality.
    """

    rule_index: int
    type: RuleType
    label: CriterionLabel
    state: Literal["same_pass", "same_fail", "left_only_pass", "right_only_pass"]
    left: CriterionSide
    right: CriterionSide
    evidence_delta: dict | None = None


class DiffLine(BaseModel):
    """One line of a line-level difference (context / added / removed)."""

    kind: Literal["context", "added", "removed"]
    left_line: str | None = Field(default=None, description="Line from the left text (None for added lines).")
    right_line: str | None = Field(default=None, description="Line from the right text (None for removed lines).")


class OutputSummary(BaseModel):
    """Bounded, factual summary of how two outputs differ.

    Lengths always refer to the ORIGINAL (untruncated) outputs, so the numbers
    stay factual even when the diff inputs were capped (``truncated``).
    """

    identical: bool
    length_a: int = Field(..., description="Original left output length.")
    length_b: int = Field(..., description="Original right output length.")
    length_delta: int = Field(..., description="right length - left length.")
    added_lines: int
    removed_lines: int
    unchanged_lines: int
    common_prefix_len: int
    common_suffix_len: int
    diff: list[DiffLine]
    truncated: bool = Field(..., description="True when either side exceeded MAX_DIFF_CHARS and was capped.")


class FieldDiff(BaseModel):
    """One metadata field compared between the two sides."""

    left: str | int | None
    right: str | int | None
    changed: bool


class UsageFieldDiff(BaseModel):
    """One usage counter compared between the two sides (null-safe)."""

    left: int | None
    right: int | None
    changed: bool


class UsageDiff(BaseModel):
    """Token usage compared counter-by-counter. Never fabricated: missing
    counters stay ``None``."""

    prompt_tokens: UsageFieldDiff
    completion_tokens: UsageFieldDiff
    total_tokens: UsageFieldDiff


class MetadataDiff(BaseModel):
    """Execution metadata compared field-by-field.

    Every field uses ``{left, right, changed}``. Latency additionally exposes
    ``latency_delta`` (right - left) ONLY when both sides reported a latency.
    Score additionally exposes ``score_delta`` (right - left, rounded to 2
    decimals) ONLY when both sides reported a score. Latency / token / score
    numbers are never described as "better".
    """

    provider: FieldDiff
    model: FieldDiff
    finish_reason: FieldDiff
    latency_ms: FieldDiff
    latency_delta: int | None = None
    score_delta: float | None = Field(
        default=None,
        description="Right score minus left score, when both sides reported one. "
        "A factual difference, never a verdict on which side is better.",
    )
    usage: UsageDiff | None = Field(default=None, description="None when neither side reported usage.")


class PromptDiff(BaseModel):
    """How the executed user prompt differed between two runs.

    ``present`` is always True when the object exists (the executed prompts were
    extractable from both runs' input snapshots); when a snapshot is missing or
    malformed the service returns ``prompt_diff=None`` instead of guessing.
    """

    present: bool = True
    identical: bool
    left_length: int
    right_length: int
    length_delta: int
    diff: list[DiffLine]


class ExecutionOutput(BaseModel):
    """Bounded output text per side."""

    text: str = Field(..., description="Output truncated to MAX_OUTPUT_DISPLAY for display.")
    length: int = Field(..., description="Original full output length (untouched by truncation).")
    truncated: bool


class ExecutionSummary(BaseModel):
    """Bounded identifying information for one side of the comparison."""

    run_id: UUID | None
    provider: str | None
    model: str | None
    latency_ms: int | None
    finish_reason: str | None
    score: float | None = Field(
        default=None,
        description="Deterministic criteria-passed percentage of this side's "
        "evaluation (Phase 3K), or null when it reported none. Factual metadata.",
    )
    scoring_mode: str | None = Field(
        default=None,
        description="Scoring profile mode that produced this side's score "
        "('unweighted' or 'weighted'). Exposed so differently-profiled sides "
        "are never silently compared as if they shared one semantics.",
    )
    version_id: UUID | None = Field(
        default=None,
        description="Exact PromptVersion this side executed, when the execution "
        "was version-scoped (Phase 3O). Factual metadata, never a verdict.",
    )
    usage: EvaluationUsage | None
    output: ExecutionOutput


class ComparisonResult(BaseModel):
    """The full, stateless result of comparing two evaluation results.

    Deliberately winner-free: no ranking, no recommendation, no "better" field
    anywhere in the shape. Per-side scores and their factual delta are exposed as
    metadata only.
    """

    left_execution: ExecutionSummary
    right_execution: ExecutionSummary
    evaluator_compatibility: EvaluatorCompatibility
    criterion_diffs: list[CriterionDiff]
    output_summary: OutputSummary
    metadata_diff: MetadataDiff
    prompt_diff: PromptDiff | None = Field(default=None, description="None when executed prompts are unavailable.")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Re-exported for convenience: supplied mode accepts already-evaluated results.
__all__ = [
    "MAX_DIFF_CHARS",
    "MAX_DIFF_HUNKS",
    "MAX_DIFF_LINE_LENGTH",
    "MAX_OUTPUT_DISPLAY",
    "MAX_SUPPLIED_OUTPUT",
    "ComparisonResult",
    "CriterionDiff",
    "CriterionLabel",
    "CriterionSide",
    "DiffLine",
    "EvaluationResult",
    "EvaluatorCompatibility",
    "ExecutionOutput",
    "ExecutionSummary",
    "FieldDiff",
    "FieldMismatch",
    "MetadataDiff",
    "OutputSummary",
    "PromptDiff",
    "UsageDiff",
    "UsageFieldDiff",
]