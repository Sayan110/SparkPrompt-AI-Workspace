"""Phase 3D: prompt testing API routes.

``POST /api/testing/run`` executes a prompt against the Phase 2 gateway through the
``PromptTestingService`` — the route never touches ``AIGateway`` directly and never
talks to a provider. It reuses the exact error envelope and gateway dependency the
AI and intelligence routes use, so provider failures surface identically. Execution
metadata is returned as the normalized ``PromptTestResult``; scoring/evaluation is
out of scope for Phase 3D.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.ai.errors import AIGatewayError, AIProviderError
from app.ai.gateway import AIGateway
from app.api.routes.ai import _http_from_error, get_gateway
from app.core.database import get_db
from app.schemas.testing import PromptTestRequest
from app.services import prompts as prompts_service
from app.testing import PromptTestingError, PromptTestingService, PromptTestingValidationError
from app.testing.types import PromptTestResult

router = APIRouter()


@router.post("/run", response_model=PromptTestResult)
def run_prompt_test(
    payload: PromptTestRequest,
    db: Session = Depends(get_db),
    gateway: AIGateway = Depends(get_gateway),
) -> PromptTestResult:
    """Execute the prompt through the AI gateway and return the normalized result."""
    # A client-supplied prompt_id is persisted as a PromptRun row (FK to prompts.id),
    # so the id must exist and belong to the authenticated owner before anything
    # executes — otherwise the gateway commit would surface as an unguarded
    # IntegrityError. NotFoundError is mapped to a clean 404 by the global
    # handler in main.py (identical for unknown and foreign ids).
    if payload.prompt_id is not None:
        prompts_service.get_prompt(db, payload.prompt_id)

    service = PromptTestingService(gateway)
    try:
        return service.run(
            prompt=payload.prompt,
            test_input=payload.input,
            provider=payload.provider,
            model=payload.model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            prompt_id=payload.prompt_id,
            version_id=payload.version_id,
            db=db,
        )
    except (AIGatewayError, AIProviderError) as exc:
        raise _http_from_error(exc) from None
    except PromptTestingValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "code": "invalid_test_request", "message": str(exc)},
        ) from None
    except PromptTestingError as exc:
        # Safety net for any future domain error siblings: sanitized 500, never raw
        # internals. Presently unreachable because PromptTestingValidationError is the
        # only subclass, but keeps new domain errors from leaking by accident.
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "code": "test_execution_failed",
                "message": "Prompt testing failed unexpectedly.",
            },
        ) from None