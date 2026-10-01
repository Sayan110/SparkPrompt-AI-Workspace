"""Prompt evaluation service (Phase 3E).

Stateless, deterministic evaluation foundation. Evaluation consumes either an existing
PromptRun (MODE A) or a fresh PromptTestingService execution (MODE B), applies the
user-authored deterministic rules to the generated output, and returns per-criterion
PASS/FAIL verdicts with bounded evidence plus an aggregate PASS/FAIL.

Guarantees:

* The service never imports or instantiates a provider adapter, the gateway, the
  router, or the registry. MODE B delegates to the injected ``PromptTestingService``;
  MODE A reads a run through the service-layer ``get_owned_run`` accessor, imported
  lazily so importing ``app.evaluation`` at module level never pulls in the AI stack.
* The deterministic checker layer is completely AI-free (see
  :mod:`app.evaluation.checkers`).
* MODE A never modifies the run. MODE B persists a run only when the execution carries
  a ``prompt_id`` (the existing gateway mechanism). No evaluation tables exist, and the
  result is stateless.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.evaluation.checkers import evaluate
from app.evaluation.errors import EvaluationValidationError
from app.evaluation.types import (
    EvaluationExecution,
    EvaluationResult,
    EvaluationUsage,
    EvaluatorConfig,
    ScoringProfile,
    default_scoring_profile,
)
from app.evaluation.scoring import validate_scoring_coverage

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.testing.service import PromptTestingService


class EvaluationService:
    """Applies deterministic rules to a PromptRun or a fresh execution."""

    def __init__(self, test_service: "PromptTestingService", run_resolver=None):
        self._test_service = test_service
        # Internal seam: defaults to the owned-run accessor at call time; exists so
        # MODE A can run against duck-typed objects in tests without a database.
        self._run_resolver = run_resolver

    @property
    def test_service(self) -> "PromptTestingService":
        return self._test_service

    def evaluate(
        self,
        *,
        evaluator: EvaluatorConfig,
        db: "Session | None" = None,
        run_id: "UUID | None" = None,
        execution: "EvaluationExecution | None" = None,
        scoring: "ScoringProfile | None" = None,
    ) -> EvaluationResult:
        """Evaluate exactly one target (run_id XOR execution).

        ``scoring`` selects the aggregation profile (default unweighted, i.e.
        Phase 3K behavior). Coverage is validated before anything executes, so
        a malformed profile fails before provider contact.
        """
        if (run_id is None) == (execution is None):
            raise EvaluationValidationError("Provide exactly one of run_id or execution.")
        active = scoring if scoring is not None else default_scoring_profile()
        try:
            validate_scoring_coverage([rule.id for rule in evaluator.rules], active)
        except ValueError as exc:
            raise EvaluationValidationError(str(exc)) from None
        if run_id is not None:
            return self._evaluate_run(db, run_id, evaluator, active)
        return self._evaluate_fresh(db, execution, evaluator, active)

    def _resolve_run(self, db: "Session | None", run_id: UUID):
        if self._run_resolver is not None:
            return self._run_resolver(db, run_id)
        from app.services.prompt_runs import get_owned_run  # lazy: keeps app.evaluation AI-free at import time

        return get_owned_run(db, run_id)

    def _evaluate_run(
        self, db: "Session | None", run_id: UUID, evaluator: EvaluatorConfig,
        scoring: "ScoringProfile",
    ) -> EvaluationResult:
        run = self._resolve_run(db, run_id)
        output = getattr(run, "output_text", None)
        if output is None or not output.strip():
            raise EvaluationValidationError("The selected run has no output to evaluate.")
        verdicts = evaluate(output, evaluator.rules, evaluator.expected_output)
        return EvaluationResult(
            run_id=getattr(run, "id", None),
            version_id=getattr(run, "version_id", None),
            output=output,
            provider=getattr(run, "provider", None),
            model=getattr(run, "model", None),
            latency_ms=getattr(run, "latency_ms", None),
            usage=_usage_from_snapshot(getattr(run, "usage_json", None)),
            evaluator_snapshot=evaluator,
            verdicts=verdicts,
            passed=all(verdict.passed for verdict in verdicts),
            scoring=scoring,
        )

    def _evaluate_fresh(
        self,
        db: "Session | None",
        execution: "EvaluationExecution",
        evaluator: EvaluatorConfig,
        scoring: "ScoringProfile",
    ) -> EvaluationResult:
        # version_id travels only when the execution carries one, so duck-typed
        # test services (unit tests) keep working with the pre-3O run() contract.
        # getattr guards duck-typed executions predating the field (same pattern
        # the service already uses for duck-typed runs).
        run_kwargs = dict(
            prompt=execution.prompt,
            test_input=execution.test_input,
            provider=execution.provider,
            model=execution.model,
            temperature=execution.temperature,
            max_tokens=execution.max_tokens,
            prompt_id=execution.prompt_id,
            db=db,
        )
        exec_version_id = getattr(execution, "version_id", None)
        if exec_version_id is not None:
            run_kwargs["version_id"] = exec_version_id
        result = self._test_service.run(**run_kwargs)
        usage = None
        if result.usage is not None:
            usage = EvaluationUsage(
                prompt_tokens=result.usage.prompt_tokens,
                completion_tokens=result.usage.completion_tokens,
                total_tokens=result.usage.total_tokens,
            )
        verdicts = evaluate(result.output, evaluator.rules, evaluator.expected_output)
        return EvaluationResult(
            run_id=result.run_id,
            version_id=getattr(result, "version_id", None),
            output=result.output,
            provider=result.provider,
            model=result.model,
            latency_ms=result.latency_ms,
            usage=usage,
            evaluator_snapshot=evaluator,
            verdicts=verdicts,
            passed=all(verdict.passed for verdict in verdicts),
            scoring=scoring,
        )


def _usage_from_snapshot(snapshot: dict | None) -> EvaluationUsage | None:
    if not snapshot:
        return None
    return EvaluationUsage(
        prompt_tokens=snapshot.get("prompt_tokens"),
        completion_tokens=snapshot.get("completion_tokens"),
        total_tokens=snapshot.get("total_tokens"),
    )