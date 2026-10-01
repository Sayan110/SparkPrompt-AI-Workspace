"""Phase 3F: prompt comparison API routes.

``POST /api/comparisons/run`` describes how two already-produced evaluation
results differ — positional criterion states, bounded output/prompt diffs, and
metadata differences — never which is better. Exactly one target is accepted:
a run pair (``left_run_id`` + ``right_run_id`` + ``evaluator``, re-evaluated so
BOTH sides use the SAME deterministic rules via the existing evaluation service,
no provider contact) or two supplied ``left_evaluation`` / ``right_evaluation``
results (no DB, no provider). The route mirrors the evaluation/testing route
conventions: the same gateway dependency, the same error envelope for
unexpected gateway failures, and a sanitized 500 for unexpected comparison
errors. Comparison is stateless: no tables, no persistence, no scoring.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.ai.errors import AIGatewayError, AIProviderError
from app.ai.gateway import AIGateway
from app.api.routes.ai import _http_from_error, get_gateway
from app.comparison import (
    ComparisonError,
    ComparisonResult,
    ComparisonService,
    ComparisonValidationError,
)
from app.core.database import get_db
from app.evaluation import EvaluationService
from app.schemas.comparison import ComparisonRequest
from app.testing import PromptTestingService

router = APIRouter()


@router.post("/run", response_model=ComparisonResult)
def run_comparison(
    payload: ComparisonRequest,
    db: Session = Depends(get_db),
    gateway: AIGateway = Depends(get_gateway),
) -> ComparisonResult:
    """Compare two evaluation results (run pair or supplied) factually."""
    service = ComparisonService(EvaluationService(PromptTestingService(gateway)))
    try:
        return service.compare(
            left_run_id=payload.left_run_id,
            right_run_id=payload.right_run_id,
            evaluator=payload.evaluator,
            left_evaluation=payload.left_evaluation,
            right_evaluation=payload.right_evaluation,
            db=db,
        )
    except (AIGatewayError, AIProviderError) as exc:
        # Kept for parity with the evaluation/testing routes; run-pair and
        # supplied comparison never contact a provider, so this only guards
        # accidental future execution paths.
        raise _http_from_error(exc) from None
    except ComparisonValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "code": "invalid_comparison_request",
                "message": str(exc),
            },
        ) from None
    except ComparisonError as exc:
        del exc  # never leak internals
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "code": "comparison_failed",
                "message": "Prompt comparison failed unexpectedly.",
            },
        ) from None