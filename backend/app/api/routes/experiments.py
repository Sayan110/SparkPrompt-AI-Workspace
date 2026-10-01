"""Phase 3H: prompt experiment API route.

``POST /api/experiments/run`` measures ONE ``EvaluatorConfig`` across EVERY persisted
``PromptVersion`` of ONE owned prompt, returning integer counts (``total_versions`` /
``passed``) plus each per-version ``EvaluationResult`` unchanged, ordered by ascending
``version_number``.

Like the Phase 3E/3F/3G routes, this route never talks to a provider directly: each
version is executed through the composed ``EvaluationService`` -> ``PromptTestingService``
-> gateway path, and the exact same error envelope is reused, so gateway/provider
failures surface identically (e.g. 409 ``provider_unavailable``). An unknown or foreign
prompt propagates untouched to the global 404 handler. Experiments are stateless: no
tables, no persistence, no scoring, no ranking, no LLM-as-judge, and no prompt or version
record is ever modified.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.ai.errors import AIGatewayError, AIProviderError
from app.ai.gateway import AIGateway
from app.api.routes.ai import _http_from_error, get_gateway
from app.core.database import get_db
from app.evaluation import EvaluationService
from app.evaluation.types import EvaluatorConfig
from app.experiments import (
    ExperimentError,
    ExperimentResult,
    ExperimentService,
    ExperimentValidationError,
)
from app.schemas.experiments import ExperimentRequest
from app.testing import PromptTestingService

router = APIRouter()


@router.post("/run", response_model=ExperimentResult)
def run_experiment(
    payload: ExperimentRequest,
    db: Session = Depends(get_db),
    gateway: AIGateway = Depends(get_gateway),
) -> ExperimentResult:
    """Evaluate every saved version of one owned prompt; report integer counts only."""
    service = ExperimentService(EvaluationService(PromptTestingService(gateway)))
    try:
        # NotFoundError (unknown/foreign prompt) propagates untouched to the global 404
        # handler — ownership-first resolution means no version is read, no provider is
        # contacted, and no partial result is produced. The experiment never modifies the
        # prompt or its versions and never persists anything itself.
        return service.run(
            prompt_id=payload.prompt_id,
            evaluator=payload.evaluator,
            db=db,
        )
    except (AIGatewayError, AIProviderError) as exc:
        raise _http_from_error(exc) from None
    except ExperimentValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "code": "invalid_experiment_request",
                "message": str(exc),
            },
        ) from None
    except ExperimentError as exc:
        # Safety net for any future domain-error sibling: sanitized 500, never internals.
        del exc
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "code": "experiment_failed",
                "message": "Prompt experiment failed unexpectedly.",
            },
        ) from None
