"""Phase 3C: prompt intelligence API routes.

``analyze`` shipped in Phase 3B; ``enhance`` and ``create`` arrive here. All three
reuse the Phase 2 gateway dependency and the same error mapping — there is no second
provider system, and the intelligence layer never talks to a provider or the databases
directly. A ``not_implemented`` mapping is kept as a defensive branch only; no
capability raises it today.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.ai.errors import AIGatewayError, AIProviderError
from app.ai.gateway import AIGateway
from app.api.routes.ai import _http_from_error, get_gateway
from app.core.database import get_db
from app.intelligence import (
    IntelligenceNotImplementedError,
    IntelligenceResponseError,
    IntelligenceValidationError,
    PromptAnalysis,
    PromptCreation,
    PromptEnhancement,
    PromptIntelligenceService,
)
from app.schemas.intelligence import (
    IntelligenceAnalyzeRequest,
    IntelligenceCreateRequest,
    IntelligenceEnhanceRequest,
)

router = APIRouter()


def _http_from_intelligence_error(exc: Exception) -> HTTPException:
    """Map intelligence-domain errors to the same HTTP envelope analyze uses."""
    if isinstance(exc, (AIGatewayError, AIProviderError)):
        return _http_from_error(exc)
    if isinstance(exc, IntelligenceValidationError):
        return HTTPException(
            status_code=400,
            detail={"status": "error", "code": "invalid_prompt", "message": str(exc)},
        )
    if isinstance(exc, IntelligenceResponseError):
        return HTTPException(
            status_code=502,
            detail={
                "status": "error",
                "code": "invalid_ai_response",
                "message": str(exc),
            },
        )
    if isinstance(exc, IntelligenceNotImplementedError):
        return HTTPException(
            status_code=501,
            detail={"status": "error", "code": "not_implemented", "message": str(exc)},
        )
    raise exc


@router.post("/analyze", response_model=PromptAnalysis)
def analyze_prompt(
    payload: IntelligenceAnalyzeRequest,
    db: Session = Depends(get_db),
    gateway: AIGateway = Depends(get_gateway),
) -> PromptAnalysis:
    """Analyze a prompt through the AI gateway and return the structured critique."""
    service = PromptIntelligenceService(gateway)
    try:
        return service.analyze(
            prompt=payload.prompt,
            provider=payload.provider,
            model=payload.model,
            db=db,
        )
    except (AIGatewayError, AIProviderError, IntelligenceValidationError,
            IntelligenceResponseError, IntelligenceNotImplementedError) as exc:
        raise _http_from_intelligence_error(exc) from None


@router.post("/enhance", response_model=PromptEnhancement)
def enhance_prompt(
    payload: IntelligenceEnhanceRequest,
    db: Session = Depends(get_db),
    gateway: AIGateway = Depends(get_gateway),
) -> PromptEnhancement:
    """Improve a prompt through the AI gateway and return the rewritten version."""
    service = PromptIntelligenceService(gateway)
    try:
        return service.enhance(
            prompt=payload.prompt,
            provider=payload.provider,
            model=payload.model,
            db=db,
        )
    except (AIGatewayError, AIProviderError, IntelligenceValidationError,
            IntelligenceResponseError, IntelligenceNotImplementedError) as exc:
        raise _http_from_intelligence_error(exc) from None


@router.post("/create", response_model=PromptCreation)
def create_prompt(
    payload: IntelligenceCreateRequest,
    db: Session = Depends(get_db),
    gateway: AIGateway = Depends(get_gateway),
) -> PromptCreation:
    """Draft a prompt from a goal through the AI gateway and return it."""
    service = PromptIntelligenceService(gateway)
    try:
        return service.create(
            goal=payload.goal,
            context=payload.context,
            provider=payload.provider,
            model=payload.model,
            db=db,
        )
    except (AIGatewayError, AIProviderError, IntelligenceValidationError,
            IntelligenceResponseError, IntelligenceNotImplementedError) as exc:
        raise _http_from_intelligence_error(exc) from None