"""Prompt experiment service (Phase 3H).

Applies ONE ``EvaluatorConfig`` to EVERY persisted ``PromptVersion`` of a single owned
prompt and returns :class:`~app.experiments.types.ExperimentResult` with integer counts
(``total_versions``/``passed``) plus each per-version ``EvaluationResult`` unchanged,
ordered by ascending ``version_number``.

Guarantees mirror the Phase 3E/3F/3G services:

* **Composition, not reimplementation**: every version is executed through the existing
  ``EvaluationService`` in MODE B (a fresh execution). The experiment contributes
  orchestration, ordering, and counting only — it never re-implements checkers,
  evaluator logic, prompt testing, provider logic, or gateway logic.
* **Ownership-first**: the prompt is resolved through the existing ownership-scoped
  prompt accessor BEFORE any version is read and before any provider is contacted, so
  an unknown or foreign prompt fails the whole experiment with the standard 404 and
  ZERO partial work. Existence is never revealed.
* **No partial work on invalid data**: every version body is validated up front, so a
  stored version that cannot be executed is rejected cleanly BEFORE the first provider
  call instead of leaving a half-finished experiment behind.
* **Exact ordering**: versions are read with ``ORDER BY version_number ASC`` (with the
  primary key only as a tie-breaker for duplicate version numbers). Insertion order,
  UUID order, and ``created_at`` are never the ordering mechanism.
* **Fresh execution per version**: each version body is converted into a fresh
  ``EvaluationExecution`` carrying the prompt id, so the existing gateway mechanism may
  record a normal ``PromptRun``. The experiment itself persists nothing.
* **Bounded**: at most ``MAX_EXPERIMENT_VERSIONS`` versions per experiment; an oversized
  prompt is rejected, never truncated.
* **AI-free at import time**: the owned-prompt accessor and the evaluation service are
  resolved lazily, so importing ``app.experiments`` never pulls in a provider adapter,
  the gateway, the router, or the registry.
* **Sequential by design**: versions are evaluated in order, one at a time, for
  deterministic output and predictable provider usage. No task queue, worker pool, or
  concurrency library is involved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from app.evaluation.service import EvaluationService
from app.evaluation.types import EvaluationExecution, EvaluatorConfig
from app.experiments.errors import ExperimentValidationError
from app.experiments.types import MAX_EXPERIMENT_VERSIONS, ExperimentResult
from app.models import PromptVersion
from app.testing.service import MAX_PROMPT_LENGTH

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class ExperimentService:
    """Measures one evaluator across every persisted version of one owned prompt."""

    def __init__(self, eval_service: "EvaluationService", prompt_resolver=None):
        self._eval_service = eval_service
        # Internal seam: defaults to the owned-prompt accessor at call time; exists so an
        # experiment can run against duck-typed prompts/versions in tests without a
        # database. Mirrors the Phase 3G suite's run-resolver seam.
        self._prompt_resolver = prompt_resolver

    def _resolve_prompt(self, db: "Session | None", prompt_id: UUID):
        """Resolve an owned prompt, defaulting to the lazy-imported accessor."""
        if self._prompt_resolver is not None:
            return self._prompt_resolver(db, prompt_id)
        # Lazy: keeps app.experiments free of the service/persistence layer at import
        # time, exactly as the Phase 3E/3G services treat the owned-run accessor.
        from app.services.prompts import get_prompt

        return get_prompt(db, prompt_id)

    def _load_versions(self, db: "Session | None", prompt_id: UUID) -> list:
        """Read every persisted version of the prompt, ascending by version_number.

        ``version_number`` is the ordering axis (the chronological version axis the user
        saved). ``id`` appears only as a deterministic tie-breaker for the
        schema-possible duplicate version number; ``created_at`` is never used.
        """
        if db is None:
            raise ExperimentValidationError(
                "An experiment requires a database session."
            )
        statement = (
            select(PromptVersion)
            .where(PromptVersion.prompt_id == prompt_id)
            .order_by(PromptVersion.version_number.asc(), PromptVersion.id.asc())
        )
        return list(db.scalars(statement))

    def _execution_for(self, prompt_id: UUID, version) -> EvaluationExecution:
        """Convert one stored version into a fresh execution, preserving its body.

        The stored ``body`` is forwarded verbatim — never reconstructed, trimmed, or
        templated. The prompt id rides along so the existing gateway mechanism may
        record a normal PromptRun for the execution. The version id rides along so
        that run permanently identifies this exact version (Phase 3O) — resolved
        from the already-ownership-checked prompt load, never caller-asserted.
        """
        body = version.body or ""
        version_number = getattr(version, "version_number", None)
        if not body.strip():
            raise ExperimentValidationError(
                f"Version {version_number} has no stored prompt body to evaluate."
            )
        if len(body) > MAX_PROMPT_LENGTH:
            raise ExperimentValidationError(
                f"Version {version_number} exceeds the maximum prompt length "
                f"({MAX_PROMPT_LENGTH} characters)."
            )
        return EvaluationExecution(
            prompt=body, prompt_id=prompt_id,
            # getattr: duck-typed versions in unit tests predate version identity.
            version_id=getattr(version, "id", None),
        )

    def run(
        self,
        *,
        prompt_id: UUID,
        evaluator: EvaluatorConfig,
        db: "Session | None" = None,
    ) -> ExperimentResult:
        """Evaluate every persisted version with one evaluator; integer counts only.

        Order of operations (each step completes before the next begins):

        1. **ownership-first** — resolve the owned prompt (404 for unknown/foreign)
        2. read ALL versions, ascending by ``version_number``
        3. enforce the ``MAX_EXPERIMENT_VERSIONS`` bound (clean error, never truncate)
        4. build every execution, validating every version body up front
        5. evaluate sequentially, in version order, via the composed
           ``EvaluationService`` in MODE B
        """
        # 1. Ownership-first: no version is read and no provider is contacted until the
        # prompt is proven owned. NotFoundError propagates untouched to the global 404.
        prompt = self._resolve_prompt(db, prompt_id)

        # 2. Every persisted version, chronologically ordered.
        versions = self._load_versions(db, prompt.id)

        # 3. Bounded: reject an oversized prompt rather than silently selecting a subset.
        if len(versions) > MAX_EXPERIMENT_VERSIONS:
            raise ExperimentValidationError(
                f"An experiment supports at most {MAX_EXPERIMENT_VERSIONS} versions; "
                f"this prompt has {len(versions)}."
            )

        # 4. Validate and build every execution BEFORE evaluating any, so unusable
        # version data cannot leave a partially-executed experiment behind.
        executions = [
            self._execution_for(prompt.id, version) for version in versions
        ]

        # 5. Sequential, in version order, through the existing evaluation path.
        evaluations = [
            self._eval_service.evaluate(
                evaluator=evaluator,
                execution=execution,
                db=db,
            )
            for execution in executions
        ]

        return ExperimentResult(
            total_versions=len(evaluations),
            passed=sum(1 for result in evaluations if result.passed),
            evaluations=evaluations,
        )
