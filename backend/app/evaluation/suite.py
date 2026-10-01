"""Evaluation suite service (Phase 3G).

Applies ONE ``EvaluatorConfig`` to MULTIPLE targets — existing owned PromptRuns
(MODE A) and/or fresh ``EvaluationExecution`` definitions (MODE B) — and returns
:class:`EvaluationSuiteResult` with integer counts (``total``/``passed``) plus
each per-target :class:`EvaluationResult` unchanged, in the request's order.

Guarantees mirror the Phase 3E/3F services:

* **Composition**: the suite is built on the existing ``EvaluationService`` and
  delegates every target to its ``evaluate`` — the suite adds orchestration and
  counting only, never a new scoring/ranking/judging path.
* **Ownership-first**: ALL MODE A run ids are resolved (via the owned-run
  accessor) BEFORE any target is evaluated, so an unknown or foreign run fails
  the whole suite with the same 404 and ZERO partial evaluation — the suite
  never reveals which runs exist.
* **No provider contact for MODE A**: run-pair evaluation never needs the AI
  stack; run resolution is imported lazily so importing this module never pulls
  in providers, the gateway, the router, or the registry.
* **Double enforcement**: the wire schema already rejects blank/duplicate/
  oversized targets (422); the service re-checks the same invariants (400) so
  direct callers get the identical contract.
* **Stateless**: no tables, no persistence, no embeddings, no LLM-as-judge.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import UUID

from app.evaluation.errors import EvaluationValidationError
from app.evaluation.service import EvaluationService
from app.evaluation.suite_types import (
    MAX_SUITE_TARGETS,
    EvaluationSuiteResult,
    EvaluationSuiteTarget,
)
from app.evaluation.types import EvaluatorConfig

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class EvaluationSuiteService:
    """Runs one evaluator over multiple targets; reports integer counts only."""

    def __init__(self, eval_service: "EvaluationService", run_resolver=None):
        self._eval_service = eval_service
        # Internal seam: defaults to the lazy-imported owned-run accessor. Tests
        # inject a fake resolver to exercise MODE A without a database.
        self._run_resolver = run_resolver

    def _resolve_run(self, db: "Session | None", run_id: UUID):
        """Resolve an owned run, defaulting to the lazy-imported accessor."""
        if self._run_resolver is not None:
            return self._run_resolver(db, run_id)
        from app.services.prompt_runs import get_owned_run  # lazy: keeps app.evaluation AI-free at import time

        return get_owned_run(db, run_id)

    def run_suite(
        self,
        *,
        evaluator: EvaluatorConfig,
        targets: list[EvaluationSuiteTarget],
        db: "Session | None" = None,
    ) -> EvaluationSuiteResult:
        """Evaluate every target with the same evaluator; integer counts only.

        Validation order (mirrors Phase 3E double-enforcement):

        1. non-empty, bounded to ``MAX_SUITE_TARGETS``
        2. per-target XOR (run_id XOR execution)
        3. no duplicate run_ids, no duplicate executions
        4. ownership-first: resolve EVERY MODE A run id before evaluating any
        5. ordered evaluate pass via the composed ``EvaluationService``
        """
        if not targets:
            raise EvaluationValidationError(
                "An evaluation suite requires at least one target."
            )
        if len(targets) > MAX_SUITE_TARGETS:
            raise EvaluationValidationError(
                f"An evaluation suite supports at most {MAX_SUITE_TARGETS} targets."
            )

        seen_run_ids: set[str] = set()
        seen_executions: set[str] = set()
        for target in targets:
            # Per-target XOR (defensive; the domain model enforces it too).
            if (target.run_id is None) == (target.execution is None):
                raise EvaluationValidationError(
                    "Provide exactly one of run_id or execution."
                )
            if target.run_id is not None:
                key = str(target.run_id)
                if key in seen_run_ids:
                    raise EvaluationValidationError(
                        "Duplicate run_id in suite targets."
                    )
                seen_run_ids.add(key)
            else:
                execution = target.execution
                assert execution is not None  # XOR guaranteed above
                key = json.dumps(
                    execution.model_dump(mode="json"), sort_keys=True
                )
                if key in seen_executions:
                    raise EvaluationValidationError(
                        "Duplicate execution in suite targets."
                    )
                seen_executions.add(key)

        # Ownership-first: resolve EVERY MODE A run id before evaluating anything,
        # so an unknown/foreign run fails the whole suite with no partial work.
        for target in targets:
            if target.run_id is not None:
                self._resolve_run(db, target.run_id)

        # Ordered evaluate pass: same evaluator, same composed evaluation service.
        evaluations = []
        for target in targets:
            if target.run_id is not None:
                evaluations.append(
                    self._eval_service.evaluate(
                        evaluator=evaluator,
                        run_id=target.run_id,
                        db=db,
                    )
                )
            else:
                execution = target.execution
                assert execution is not None  # XOR guaranteed above
                evaluations.append(
                    self._eval_service.evaluate(
                        evaluator=evaluator,
                        execution=execution,
                        db=db,
                    )
                )

        return EvaluationSuiteResult(
            total=len(evaluations),
            passed=sum(1 for result in evaluations if result.passed),
            evaluations=evaluations,
        )