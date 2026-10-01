"""Durable evaluation history — persistence and reads (Phase 3P).

This is the only module that writes an :class:`EvaluationRecord`, and it writes
exactly one kind of thing: a finished, already-computed evaluation. Nothing here
re-runs a provider, re-applies a rule, or re-derives a score — the verdicts,
evaluator snapshot, scoring snapshot, and score are copied **by value** from the
result the API just returned, so later changes to prompt bodies, rules, or
weights cannot rewrite what happened.

Ownership follows the project's existing chain (User → Project → Prompt →
EvaluationRecord) using the same workspace scoping as ``get_owned_run``, so an
unknown id and a foreign id are the same 404 and never leak each other's
existence.

Reads are pure: they select stored rows (plus immutable PromptVersion /
PromptRun columns joined for display) and never construct a provider, a
gateway, or an evaluator.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evaluation.errors import EvaluationError
from app.evaluation.types import (
    CriterionVerdict,
    EvaluationResult,
    EvaluationUsage,
    EvaluatorConfig,
    ScoringMode,
    ScoringProfile,
)
from app.models import EvaluationRecord, Prompt, PromptRun, PromptVersion, Project
from app.schemas.evaluation import EvaluationHistoryItem, EvaluationRecordRead
from app.services.errors import NotFoundError
from app.services.identity import resolve_owner
from app.services.prompt_runs import get_owned_run

MAX_EVALUATION_HISTORY = 50
"""Hard cap on one prompt's history read — no unbounded list query exists."""


def create_evaluation_record(
    db: Session, result: EvaluationResult
) -> EvaluationRecord | None:
    """Persist one finished evaluation, anchored to its owned PromptRun.

    Returns ``None`` when the evaluation has no persisted run to anchor to — a
    draft execution without ``prompt_id`` was never stored as a run, and history
    cannot reference what does not exist (no run is invented, no version is
    guessed). That is the only case persistence is impossible for.

    Identity is read from the owned run (``get_owned_run`` re-checks ownership),
    never from caller-supplied ids, so ``prompt_id`` / ``prompt_version_id`` /
    ``prompt_run_id`` inside one record cannot disagree. Snapshots and verdicts
    are serialized by value, and the score is stored exactly as returned — one
    ``commit`` so a record exists whole or not at all.
    """
    if result.run_id is None:
        return None
    # Ownership-first: an unknown/foreign run raises the existing safe 404 before
    # anything is read or written, so a foreign evaluation is never created.
    run = get_owned_run(db, result.run_id)
    version_id = getattr(run, "version_id", None)
    if result.version_id != version_id:
        # Defensive, not reachable in practice: both sides are server-resolved
        # from this same run. Writing either one would record an identity the
        # other contradicts, so the record is refused instead of persisted.
        raise EvaluationError("Evaluation identity does not match the stored run.")
    record = EvaluationRecord(
        prompt_id=run.prompt_id,
        prompt_version_id=version_id,
        prompt_run_id=run.id,
        evaluator_snapshot=result.evaluator_snapshot.model_dump(mode="json"),
        scoring_snapshot=result.scoring.model_dump(mode="json"),
        verdicts=[verdict.model_dump(mode="json") for verdict in result.verdicts],
        passed=result.passed,
        score=result.score,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _owner_project_ids(db: Session) -> set[UUID]:
    owner = resolve_owner(db)
    return set(db.scalars(select(Project.id).where(Project.user_id == owner.id)))


def get_owned_evaluation(db: Session, evaluation_id: UUID) -> EvaluationRecord:
    """Return one evaluation of this workspace, or raise ``NotFoundError``.

    Mirrors ``get_owned_run`` exactly: ownership is resolved through the record's
    prompt and its project, and an unknown id is indistinguishable from a foreign
    one (identical "Evaluation not found", no existence leak).
    """
    project_ids = _owner_project_ids(db)
    record = db.get(EvaluationRecord, evaluation_id)
    if record is None:
        raise NotFoundError("Evaluation not found")
    prompt = db.get(Prompt, record.prompt_id)
    if prompt is None or prompt.project_id not in project_ids:
        raise NotFoundError("Evaluation not found")
    return record


def _version_number(db: Session, version_id: UUID | None) -> int | None:
    """Read the immutable version number for display; never copied onto the record."""
    if version_id is None:
        return None
    version = db.get(PromptVersion, version_id)
    return version.version_number if version is not None else None


def _usage_from_snapshot(snapshot: dict | None) -> EvaluationUsage | None:
    if not snapshot:
        return None
    return EvaluationUsage(
        prompt_tokens=snapshot.get("prompt_tokens"),
        completion_tokens=snapshot.get("completion_tokens"),
        total_tokens=snapshot.get("total_tokens"),
    )


def _parse_snapshots(
    record: EvaluationRecord,
) -> tuple[EvaluatorConfig, ScoringProfile, list[CriterionVerdict]]:
    """Re-validate stored JSON into the wire contracts, or fail loudly.

    A snapshot that no longer validates (direct database tampering, not anything
    the API can produce) raises ``EvaluationError`` so the route answers with the
    existing sanitized 500 envelope. History is never silently patched, guessed
    at, or partially returned.
    """
    try:
        evaluator = EvaluatorConfig.model_validate(record.evaluator_snapshot)
        scoring = ScoringProfile.model_validate(record.scoring_snapshot)
        verdicts = [CriterionVerdict.model_validate(item) for item in record.verdicts]
    except (ValidationError, TypeError, AttributeError):
        raise EvaluationError("Stored evaluation is unreadable.") from None
    return evaluator, scoring, verdicts


def read_evaluation(db: Session, evaluation_id: UUID) -> EvaluationRecordRead:
    """Build the immutable detail payload for ONE owned evaluation.

    Purely a read: no provider, no gateway, no checker, no score recomputation.
    Execution metadata (output, provider, model, latency, usage) is joined from
    the immutable PromptRun — runs and versions are never mutated — while the
    snapshots, verdicts, passed, and score come from the stored record itself.
    """
    record = get_owned_evaluation(db, evaluation_id)
    run = db.get(PromptRun, record.prompt_run_id)
    if run is None:
        # Unreachable while the foreign key holds (a deleted run cascades the
        # record away), but never answered with half a picture.
        raise NotFoundError("Evaluation not found")
    evaluator, scoring, verdicts = _parse_snapshots(record)
    return EvaluationRecordRead(
        evaluation_id=record.id,
        prompt_id=record.prompt_id,
        prompt_version_id=record.prompt_version_id,
        version_number=_version_number(db, record.prompt_version_id),
        prompt_run_id=record.prompt_run_id,
        output=run.output_text or "",
        provider=run.provider,
        model=run.model,
        latency_ms=run.latency_ms,
        usage=_usage_from_snapshot(run.usage_json),
        evaluator_snapshot=evaluator,
        verdicts=verdicts,
        passed=record.passed,
        scoring=scoring,
        score=record.score,
        created_at=record.created_at,
    )


def _scoring_mode(snapshot: object) -> ScoringMode:
    """The stored mode that produced the stored score (never a live preference)."""
    try:
        return ScoringProfile.model_validate(snapshot).mode
    except (ValidationError, TypeError):
        raise EvaluationError("Stored evaluation is unreadable.") from None


def list_prompt_evaluations(
    db: Session, prompt_id: UUID
) -> list[EvaluationHistoryItem]:
    """Newest-first history for ONE owned prompt, bounded to the history cap.

    Ownership is resolved before anything is read, so an unknown or foreign
    prompt fails with the identical 404 and no row can leak. Ordering is
    ``created_at DESC`` with the row id as a deterministic tie-breaker; the
    ``LIMIT`` keeps the read bounded no matter how much history exists. List rows
    carry no verdict evidence — the detail endpoint owns the full snapshots.
    """
    project_ids = _owner_project_ids(db)
    prompt = db.get(Prompt, prompt_id)
    if prompt is None or prompt.project_id not in project_ids:
        raise NotFoundError("Prompt not found")
    rows = db.execute(
        select(
            EvaluationRecord,
            PromptVersion.version_number,
            PromptRun.provider,
            PromptRun.model,
        )
        .outerjoin(
            PromptVersion, EvaluationRecord.prompt_version_id == PromptVersion.id
        )
        .outerjoin(PromptRun, EvaluationRecord.prompt_run_id == PromptRun.id)
        .where(EvaluationRecord.prompt_id == prompt.id)
        .order_by(EvaluationRecord.created_at.desc(), EvaluationRecord.id.desc())
        .limit(MAX_EVALUATION_HISTORY)
    ).all()
    return [
        EvaluationHistoryItem(
            evaluation_id=record.id,
            prompt_version_id=record.prompt_version_id,
            version_number=version_number,
            prompt_run_id=record.prompt_run_id,
            score=record.score,
            passed=record.passed,
            scoring_mode=_scoring_mode(record.scoring_snapshot),
            provider=provider,
            model=model,
            created_at=record.created_at,
        )
        for record, version_number, provider, model in rows
    ]
