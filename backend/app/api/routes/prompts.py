from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.evaluation import EvaluationError
from app.schemas.common import Message
from app.schemas.evaluation import EvaluationHistoryItem
from app.schemas.prompt import PromptCreate, PromptRead, PromptUpdate, PromptVersionRead
from app.services import evaluations as evaluations_service
from app.services import prompts as prompts_service

router = APIRouter()


@router.get("", response_model=list[PromptRead])
def list_prompts(db: Session = Depends(get_db)) -> list[PromptRead]:
    return prompts_service.list_prompts(db)


@router.post("", response_model=PromptRead, status_code=status.HTTP_201_CREATED)
def create_prompt(payload: PromptCreate, db: Session = Depends(get_db)) -> PromptRead:
    return prompts_service.create_prompt(db, payload)


@router.get("/{prompt_id}", response_model=PromptRead)
def get_prompt(prompt_id: UUID, db: Session = Depends(get_db)) -> PromptRead:
    return prompts_service.get_prompt(db, prompt_id)


@router.put("/{prompt_id}", response_model=PromptRead)
def update_prompt(
    prompt_id: UUID, payload: PromptUpdate, db: Session = Depends(get_db)
) -> PromptRead:
    return prompts_service.update_prompt(db, prompt_id, payload)


@router.delete("/{prompt_id}", response_model=Message)
def delete_prompt(prompt_id: UUID, db: Session = Depends(get_db)) -> Message:
    prompts_service.delete_prompt(db, prompt_id)
    return Message(message="Prompt deleted")


@router.get("/{prompt_id}/versions", response_model=list[PromptVersionRead])
def list_prompt_versions(
    prompt_id: UUID, db: Session = Depends(get_db)
) -> list[PromptVersionRead]:
    return prompts_service.list_prompt_versions(db, prompt_id)


@router.post(
    "/{prompt_id}/versions/{version_id}/restore",
    response_model=PromptVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def restore_prompt_version(
    prompt_id: UUID, version_id: UUID, db: Session = Depends(get_db)
) -> PromptVersionRead:
    return prompts_service.restore_prompt_version(db, prompt_id, version_id)


@router.get("/{prompt_id}/evaluations", response_model=list[EvaluationHistoryItem])
def list_prompt_evaluations(
    prompt_id: UUID, db: Session = Depends(get_db)
) -> list[EvaluationHistoryItem]:
    """Bounded, newest-first history of ONE prompt's saved evaluations (Phase 3P).

    Ownership is resolved before any row is read: an unknown or foreign prompt
    is the same non-leaking 404 used everywhere else. Rows are read-only and
    carry no verdict evidence — this locates history, it never re-evaluates it.
    A stored snapshot that no longer validates (database tampering, not
    anything the API can produce) answers with the sanitized 500 envelope
    rather than a partial or silently repaired list.
    """
    try:
        return evaluations_service.list_prompt_evaluations(db, prompt_id)
    except EvaluationError as exc:
        del exc
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "code": "evaluation_failed",
                "message": "Evaluation history could not be read.",
            },
        ) from None