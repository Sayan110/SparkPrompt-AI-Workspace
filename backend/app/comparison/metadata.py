"""Execution metadata comparison (Phase 3F).

Every metadata field (provider, model, finish reason, latency, token usage) is
compared into a factual ``{left, right, changed}`` shape. Latency additionally
exposes ``latency_delta`` (right - left) ONLY when both sides reported a
latency. Usage is null-safe and never fabricated: missing counters stay
``None``, and ``usage`` is ``None`` entirely when neither side reported usage.

``finish_reason`` is not part of the Phase 3E ``EvaluationResult`` contract, so
run-pair comparison threads it through from the resolved runs; supplied
comparison has no source for it and both sides stay ``None`` (honest, not
fabricated).

This module is AI-free and imports only the comparison + evaluation contracts.
"""

from __future__ import annotations

from app.comparison.types import FieldDiff, MetadataDiff, UsageDiff, UsageFieldDiff
from app.evaluation.types import EvaluationResult


def _diff(left, right) -> FieldDiff:
    return FieldDiff(left=left, right=right, changed=left != right)


def _usage_diff(left, right) -> UsageFieldDiff:
    return UsageFieldDiff(left=left, right=right, changed=left != right)


def compare_metadata(
    left: EvaluationResult,
    right: EvaluationResult,
    left_finish_reason: str | None = None,
    right_finish_reason: str | None = None,
) -> MetadataDiff:
    """Compare execution metadata between two evaluation results."""
    latency_delta = None
    if isinstance(left.latency_ms, int) and isinstance(right.latency_ms, int):
        latency_delta = right.latency_ms - left.latency_ms

    # Same precedent as latency_delta: a factual right-minus-left difference, only
    # when both sides reported a score, rounded so no float artifact is exposed.
    score_delta = None
    if left.score is not None and right.score is not None:
        score_delta = round(right.score - left.score, 2)

    usage = None
    if left.usage is not None or right.usage is not None:
        left_usage = left.usage
        right_usage = right.usage
        usage = UsageDiff(
            prompt_tokens=_usage_diff(
                left_usage.prompt_tokens if left_usage else None,
                right_usage.prompt_tokens if right_usage else None,
            ),
            completion_tokens=_usage_diff(
                left_usage.completion_tokens if left_usage else None,
                right_usage.completion_tokens if right_usage else None,
            ),
            total_tokens=_usage_diff(
                left_usage.total_tokens if left_usage else None,
                right_usage.total_tokens if right_usage else None,
            ),
        )

    return MetadataDiff(
        provider=_diff(left.provider, right.provider),
        model=_diff(left.model, right.model),
        finish_reason=_diff(left_finish_reason, right_finish_reason),
        latency_ms=_diff(left.latency_ms, right.latency_ms),
        latency_delta=latency_delta,
        score_delta=score_delta,
        usage=usage,
    )