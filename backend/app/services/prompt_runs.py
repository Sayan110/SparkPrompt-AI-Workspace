from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PromptRun
from app.ai.types import AIRequest, AIResponse, AIUsage
from app.models import Project, Prompt
from app.services.errors import NotFoundError
from app.services.identity import resolve_owner


def get_owned_run(db: Session, run_id: UUID) -> PromptRun:
    """Return a PromptRun that belongs to the authenticated workspace, or raise NotFoundError.

    Ownership is resolved through the same workspace scoping the rest of the service
    layer uses: the run's prompt must live in one of the current owner's projects.
    Unknown and foreign runs are indistinguishable (404, no existence leak) and the
    run is never modified. Phase 3E evaluation consumes runs through this accessor.
    """
    owner = resolve_owner(db)
    owner_project_ids = set(
        db.scalars(select(Project.id).where(Project.user_id == owner.id))
    )
    run = db.get(PromptRun, run_id)
    if run is None:
        raise NotFoundError("Prompt run not found")
    prompt = db.get(Prompt, run.prompt_id)
    if prompt is None or prompt.project_id not in owner_project_ids:
        raise NotFoundError("Prompt run not found")
    return run


def record_success(
    db: Session,
    *,
    prompt_id: UUID,
    request: AIRequest,
    response: AIResponse,
    version_id: UUID | None = None,
) -> PromptRun:
    run = PromptRun(
        prompt_id=prompt_id,
        version_id=version_id,
        provider=request.provider,
        model=request.model,
        input_snapshot={
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": request.stream,
        },
        output_text=response.text,
        status="success",
        latency_ms=response.latency_ms,
        finish_reason=response.finish_reason,
        usage_json=_usage_snapshot(response.usage),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def record_failure(
    db: Session,
    *,
    prompt_id: UUID,
    provider: str,
    model: str,
    input_snapshot: dict,
    error: str,
    version_id: UUID | None = None,
) -> PromptRun:
    run = PromptRun(
        prompt_id=prompt_id,
        version_id=version_id,
        provider=provider,
        model=model,
        input_snapshot=input_snapshot,
        output_text=None,
        status="error",
        error=error[:2000],
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _usage_snapshot(usage: AIUsage | None) -> dict | None:
    if usage is None:
        return None
    return usage.to_snapshot()