"""Phases 3E and 3G: prompt evaluation API routes.

``POST /api/evaluations/run`` applies deterministic, user-authored rules to a supported
target — either an existing PromptRun (MODE A) or a fresh PromptTestingService execution
(MODE B) — and returns normalized per-criterion verdicts with bounded evidence plus an
aggregate PASS/FAIL. ``POST /api/evaluations/suite`` (Phase 3G) applies ONE evaluator to
MULTIPLE targets — existing runs and/or fresh executions — and returns integer counts
(``total``/``passed``) plus each per-target result unchanged. The routes never talk to a
provider directly and reuse the exact error envelope the AI/intelligence/testing routes
use, so fresh-execution gateway failures surface identically (e.g. 409
``provider_unavailable``). Evaluation stays deterministic: no scoring in the checker
layer, no LLM-as-judge, and nothing here talks to a provider directly.

Phase 3P (evaluation history) adds:

* ``POST /api/evaluations/run`` — the canonical creation path. After a completely
  successful evaluation it appends one durable :class:`EvaluationRecord` and echoes
  its id as ``EvaluationResult.evaluation_id``. Persistence happens strictly last
  (ownership → run resolution → validation → execution → verdicts → scoring →
  result), it is skipped when the evaluation has no persisted run to anchor to
  (draft execution), and a failed commit is rolled back and answered with the
  sanitized 500 envelope instead of a record that does not exist.
* ``GET /api/evaluations/{evaluation_id}`` — one immutable stored evaluation.
  Purely a read: no provider, no re-run, no recomputation against current rules.
* ``GET /api/prompts/{prompt_id}/evaluations`` — bounded, newest-first history.

The suite path (``POST /api/evaluations/suite``) is unchanged and still persists
nothing: Phase 3P stores individual evaluations only, never suites.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ai.errors import AIGatewayError, AIProviderError
from app.ai.gateway import AIGateway
from app.api.routes.ai import _http_from_error, get_gateway
from app.core.database import get_db
from app.evaluation import (
    EvaluationError,
    EvaluationService,
    EvaluationSuiteResult,
    EvaluationSuiteService,
    EvaluationSuiteTarget,
    EvaluationValidationError,
)
from app.evaluation.types import EvaluationExecution, EvaluationResult
from app.models import EvaluationRecord
from app.schemas.evaluation import (
    EvaluationRecordRead,
    EvaluationRequest,
    EvaluationSuiteRequest,
)
from app.services import evaluations as evaluations_service
from app.testing import PromptTestingService

router = APIRouter()


def _persist(db: Session, result: EvaluationResult) -> EvaluationRecord | None:
    """Append the history row for a finished evaluation, or answer safely.

    Runs strictly after result construction. A failure never masquerades as
    success: the transaction is rolled back (no partial record, no id that does
    not exist) and the caller gets the project's sanitized 500 envelope with no
    database detail. Ownership that fails here surfaces as the same non-leaking
    404 the run itself would produce.
    """
    try:
        return evaluations_service.create_evaluation_record(db, result)
    except EvaluationError as exc:
        del exc
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "code": "evaluation_persistence_failed",
                "message": "The evaluation could not be saved.",
            },
        ) from None
    except SQLAlchemyError as exc:
        del exc
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "code": "evaluation_persistence_failed",
                "message": "The evaluation could not be saved.",
            },
        ) from None


@router.post("/run", response_model=EvaluationResult)
def run_evaluation(
    payload: EvaluationRequest,
    db: Session = Depends(get_db),
    gateway: AIGateway = Depends(get_gateway),
) -> EvaluationResult:
    """Evaluate an existing PromptRun or a fresh execution against deterministic rules."""
    execution = None
    if payload.execution is not None:
        execution = EvaluationExecution(
            prompt=payload.execution.prompt,
            test_input=payload.execution.input,
            provider=payload.execution.provider,
            model=payload.execution.model,
            temperature=payload.execution.temperature,
            max_tokens=payload.execution.max_tokens,
            prompt_id=payload.execution.prompt_id,
            version_id=payload.execution.version_id,
        )

    service = EvaluationService(PromptTestingService(gateway))
    try:
        # NotFoundError (unknown/foreign prompt run) propagates untouched to the global
        # 404 handler — existence is never leaked. Evaluation never modifies the run.
        result = service.evaluate(
            evaluator=payload.evaluator,
            run_id=payload.run_id,
            execution=execution,
            scoring=payload.scoring,
            db=db,
        )
    except (AIGatewayError, AIProviderError) as exc:
        raise _http_from_error(exc) from None
    except EvaluationValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "code": "invalid_evaluation_request",
                "message": str(exc),
            },
        ) from None
    except EvaluationError as exc:
        # Safety net for any future domain-error sibling: sanitized 500, never internals.
        del exc
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "code": "evaluation_failed",
                "message": "Prompt evaluation failed unexpectedly.",
            },
        ) from None

    # Phase 3P: persist only after the evaluation fully succeeded (verdicts,
    # scoring, result). Nothing was persisted for a draft execution with no run.
    record = _persist(db, result)
    if record is None:
        return result
    # Copy, never mutate: evaluation_id is identity only, and the stored score is
    # echoed back without re-deriving anything.
    return result.model_copy(update={"evaluation_id": record.id})


@router.post("/suite", response_model=EvaluationSuiteResult)
def run_evaluation_suite(
    payload: EvaluationSuiteRequest,
    db: Session = Depends(get_db),
    gateway: AIGateway = Depends(get_gateway),
) -> EvaluationSuiteResult:
    """Apply ONE evaluator to MULTIPLE targets; report integer counts + per-target results."""
    targets = []
    for wire in payload.targets:
        execution = None
        if wire.execution is not None:
            execution = EvaluationExecution(
                prompt=wire.execution.prompt,
                test_input=wire.execution.input,
                provider=wire.execution.provider,
                model=wire.execution.model,
                temperature=wire.execution.temperature,
                max_tokens=wire.execution.max_tokens,
                prompt_id=wire.execution.prompt_id,
                version_id=wire.execution.version_id,
            )
        targets.append(EvaluationSuiteTarget(run_id=wire.run_id, execution=execution))

    service = EvaluationSuiteService(EvaluationService(PromptTestingService(gateway)))
    try:
        # NotFoundError (unknown/foreign prompt run) propagates untouched to the global
        # 404 handler — ownership-first resolution means no partial evaluation. The
        # suite never modifies runs and never persists anything.
        return service.run_suite(
            evaluator=payload.evaluator,
            targets=targets,
            db=db,
        )
    except (AIGatewayError, AIProviderError) as exc:
        raise _http_from_error(exc) from None
    except EvaluationValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "code": "invalid_evaluation_request",
                "message": str(exc),
            },
        ) from None
    except EvaluationError as exc:
        # Safety net for any future domain-error sibling: sanitized 500, never internals.
        del exc
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "code": "evaluation_failed",
                "message": "Prompt evaluation suite failed unexpectedly.",
            },
        ) from None


@router.get("/{evaluation_id}", response_model=EvaluationRecordRead)
def get_evaluation(
    evaluation_id: UUID, db: Session = Depends(get_db)
) -> EvaluationRecordRead:
    """Read ONE stored evaluation: snapshots, verdicts, and score exactly as saved.

    Purely a read — it never contacts a provider, never re-runs the evaluator,
    and never recomputes against the current rules. Unknown and foreign ids are
    the same non-leaking 404 (NotFoundError reaches the global handler). A
    stored snapshot that no longer validates answers with the sanitized 500
    envelope rather than a partial or silently rewritten history.
    """
    try:
        return evaluations_service.read_evaluation(db, evaluation_id)
    except EvaluationError as exc:
        del exc
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "code": "evaluation_failed",
                "message": "Stored evaluation could not be read.",
            },
        ) from None