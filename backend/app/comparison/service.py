"""Prompt comparison service (Phase 3F).

Stateless, factual, deterministic: comparison consumes two already-produced
evaluation results and describes how they differ — never which is better. The
service resolves exactly ONE target:

MODE A (run-pair)
    ``left_run_id`` + ``right_run_id`` + ``evaluator``: both owned prompt runs
    are evaluated with the SAME ``EvaluatorConfig`` through the existing
    Phase 3E ``EvaluationService`` (no provider contact — Mode A reads runs).
    The run accessor is resolved lazily and read-only; both runs are
    ownership-checked BEFORE any evaluation so unknown/foreign runs surface as
    the same 404 with no partial data.

MODE B (supplied)
    ``left_evaluation`` + ``right_evaluation``: two already-produced
    ``EvaluationResult`` objects are compared directly — no DB, no resolver.

Guarantees:

* Importing :mod:`app.comparison` never pulls in the AI stack: the run accessor
  and evaluation service are imported lazily; the pure diff modules import only
  stdlib + ``app.comparison`` (+ the AI-free evaluation contracts).
* MODE A never modifies runs and creates no rows; the result is stateless.
* The executed prompt is derived from each run's authoritative
  ``input_snapshot.messages`` — there is no prompt-version pointer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.comparison.compatibility import compare_evaluators
from app.comparison.criterion import compare_criteria
from app.comparison.diff import line_diff
from app.comparison.errors import ComparisonValidationError
from app.comparison.metadata import compare_metadata
from app.comparison.types import (
    MAX_OUTPUT_DISPLAY,
    ComparisonResult,
    ExecutionOutput,
    ExecutionSummary,
    PromptDiff,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.evaluation.service import EvaluationService
    from app.evaluation.types import EvaluationResult


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) > limit:
        return text[:limit], True
    return text, False


def _summarize(result: "EvaluationResult", finish_reason: str | None) -> ExecutionSummary:
    """Bounded identifying information for one side (output text capped for display)."""
    output_text, output_truncated = _truncate(result.output, MAX_OUTPUT_DISPLAY)
    return ExecutionSummary(
        run_id=result.run_id,
        provider=result.provider,
        model=result.model,
        latency_ms=result.latency_ms,
        finish_reason=finish_reason,
        score=result.score,
        scoring_mode=result.scoring.mode if result.scoring is not None else None,
        version_id=getattr(result, "version_id", None),
        usage=result.usage,
        output=ExecutionOutput(
            text=output_text, length=len(result.output), truncated=output_truncated
        ),
    )


def extract_executed_prompt(snapshot: Any) -> str | None:
    """Extract the executed user prompt from a run's ``input_snapshot``.

    Missing/malformed snapshots or a snapshot with no user message return None
    (the caller reports ``prompt_diff=None`` instead of guessing).
    """
    if not isinstance(snapshot, dict):
        return None
    messages = snapshot.get("messages")
    if not isinstance(messages, list):
        return None
    parts = [
        message.get("content", "")
        for message in messages
        if isinstance(message, dict)
        and message.get("role") == "user"
        and isinstance(message.get("content"), str)
    ]
    if not parts:
        return None
    return "\n".join(parts)


def build_prompt_diff(left_run: Any, right_run: Any) -> PromptDiff | None:
    """Compare the executed user prompts of two runs, or None when unavailable."""
    left_text = extract_executed_prompt(getattr(left_run, "input_snapshot", None))
    right_text = extract_executed_prompt(getattr(right_run, "input_snapshot", None))
    if left_text is None or right_text is None:
        return None
    summary = line_diff(left_text, right_text)
    return PromptDiff(
        present=True,
        identical=summary.identical,
        left_length=summary.length_a,
        right_length=summary.length_b,
        length_delta=summary.length_delta,
        diff=summary.diff,
    )


def build_comparison(
    left: "EvaluationResult",
    right: "EvaluationResult",
    *,
    prompt_diff: PromptDiff | None = None,
    left_finish_reason: str | None = None,
    right_finish_reason: str | None = None,
) -> ComparisonResult:
    """Compose every factual difference between two evaluation results.

    When the evaluator snapshots are incompatible, ``criterion_diffs`` stays
    empty and the mismatch list explains why; output / prompt / metadata
    differences are always reported.
    """
    compatibility = compare_evaluators(left.evaluator_snapshot, right.evaluator_snapshot)
    criterion_diffs = (
        compare_criteria(left, right) if compatibility.comparable else []
    )
    return ComparisonResult(
        left_execution=_summarize(left, left_finish_reason),
        right_execution=_summarize(right, right_finish_reason),
        evaluator_compatibility=compatibility,
        criterion_diffs=criterion_diffs,
        output_summary=line_diff(left.output, right.output),
        metadata_diff=compare_metadata(
            left, right, left_finish_reason, right_finish_reason
        ),
        prompt_diff=prompt_diff,
        generated_at=datetime.now(timezone.utc),
    )


class ComparisonService:
    """Resolves one comparison target and composes the factual ComparisonResult."""

    def __init__(self, eval_service: "EvaluationService", run_resolver=None):
        self._eval_service = eval_service
        # Internal seam: defaults to the lazy-imported owned-run accessor. Tests
        # inject a fake resolver to exercise MODE A without a database.
        self._run_resolver = run_resolver

    def _resolve_run(self, db: "Session | None", run_id: UUID):
        if self._run_resolver is not None:
            return self._run_resolver(db, run_id)
        from app.services.prompt_runs import get_owned_run  # lazy: keeps app.comparison AI-free

        return get_owned_run(db, run_id)

    def compare(
        self,
        *,
        db: "Session | None" = None,
        left_run_id: UUID | None = None,
        right_run_id: UUID | None = None,
        evaluator=None,
        left_evaluation: "EvaluationResult | None" = None,
        right_evaluation: "EvaluationResult | None" = None,
    ) -> ComparisonResult:
        """Compare exactly one target: a run pair or two supplied results."""
        run_mode = (
            left_run_id is not None
            or right_run_id is not None
            or evaluator is not None
        )
        supplied_mode = left_evaluation is not None or right_evaluation is not None

        if run_mode == supplied_mode:
            raise ComparisonValidationError(
                "Provide exactly one comparison target: run-pair "
                "(left_run_id, right_run_id, evaluator) or supplied "
                "(left_evaluation, right_evaluation)."
            )

        if run_mode:
            if left_run_id is None or right_run_id is None or evaluator is None:
                raise ComparisonValidationError(
                    "Run-pair comparison requires left_run_id, right_run_id, and "
                    "evaluator together."
                )
            return self._compare_runs(db, left_run_id, right_run_id, evaluator)

        if left_evaluation is None or right_evaluation is None:
            raise ComparisonValidationError(
                "Supplied comparison requires both left_evaluation and "
                "right_evaluation."
            )
        return build_comparison(left_evaluation, right_evaluation)

    def _compare_runs(
        self, db, left_run_id: UUID, right_run_id: UUID, evaluator
    ) -> ComparisonResult:
        """Evaluate two owned runs with the SAME evaluator, then compare.

        Both runs are ownership-resolved BEFORE any evaluation, so an unknown or
        foreign run surfaces as the same 404 with no partial data. Evaluation
        itself is stateless (Phase 3E MODE A reads runs; no provider contact).
        """
        left_run = self._resolve_run(db, left_run_id)
        right_run = self._resolve_run(db, right_run_id)

        left = self._eval_service.evaluate(
            run_id=left_run_id, evaluator=evaluator, db=db
        )
        right = self._eval_service.evaluate(
            run_id=right_run_id, evaluator=evaluator, db=db
        )
        return build_comparison(
            left,
            right,
            prompt_diff=build_prompt_diff(left_run, right_run),
            left_finish_reason=getattr(left_run, "finish_reason", None),
            right_finish_reason=getattr(right_run, "finish_reason", None),
        )