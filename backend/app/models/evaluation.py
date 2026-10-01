from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.prompt import Prompt, PromptRun, PromptVersion


class EvaluationRecord(Base):
    """Immutable historical evaluation artifact (Phase 3P).

    One row per performed evaluation that could be anchored to a real
    PromptRun. Snapshots (evaluator, scoring, verdicts) are stored by value so
    the record stays interpretable after rules or weights change; the score is
    the exact value returned to the user, never recomputed on read.

    Append-only by convention: no update path exists anywhere. Rows die only
    with their prompt (CASCADE everywhere, matching the project convention).
    """

    __tablename__ = "evaluation_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prompt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prompts.id", ondelete="CASCADE"), nullable=False
    )
    # Nullable: versionless (legacy or draft-adjacent) runs evaluate fine, and
    # their records must not invent a version. Set only from the run's stored
    # identity, never guessed or backfilled. The version number is never copied
    # here — reads join the immutable PromptVersion row instead, so there is no
    # second copy of the version identity to drift.
    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prompt_versions.id", ondelete="CASCADE"), nullable=True
    )
    # The run is the anchor: output, provider, model, latency, and usage are
    # referenced through it (runs have no update path), never duplicated here.
    # Every record has a run — run-less evaluations cannot be owned or listed.
    prompt_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prompt_runs.id", ondelete="CASCADE"), nullable=False
    )
    evaluator_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    scoring_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    verdicts: Mapped[list] = mapped_column(JSONB, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    prompt: Mapped[Prompt] = relationship()
    version: Mapped[PromptVersion | None] = relationship()
    run: Mapped[PromptRun] = relationship()

    __table_args__ = (
        # Serves the history list exactly: one prompt's records, newest first
        # (created_at DESC, id DESC tie-break). The only secondary index.
        Index(
            "ix_evaluation_records_prompt_history",
            "prompt_id",
            created_at.desc(),
            id.desc(),
        ),
    )
