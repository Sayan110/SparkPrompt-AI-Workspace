from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.evaluation.suite_types import MAX_SUITE_TARGETS
from app.evaluation.types import (
    CriterionVerdict,
    EvaluationResult,
    EvaluationUsage,
    EvaluatorConfig,
    ScoringMode,
    ScoringProfile,
    default_scoring_profile,
)
from app.schemas.testing import PromptTestRequest


class EvaluationRequest(BaseModel):
    """Request to run a deterministic evaluation (Phase 3E).

    Exactly one target must be supplied: ``run_id`` (evaluate an existing PromptRun) or
    ``execution`` (a fresh inline execution reusing the PromptTestRequest shape). The
    ``evaluator`` carries the user-authored rules — validated by the domain Rule
    contract (1-20 rules, per-type required fields, bounded regex, expected_output
    requirements). Invalid combinations and malformed evaluators fail FastAPI
    validation (422) before any provider is contacted.
    """

    run_id: UUID | None = None
    execution: PromptTestRequest | None = None
    evaluator: EvaluatorConfig
    scoring: ScoringProfile = Field(
        default_factory=default_scoring_profile,
        description="Optional scoring profile (default unweighted, i.e. Phase 3K "
        "behavior). Validated against the evaluator before any provider contact.",
    )

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "EvaluationRequest":
        if (self.run_id is not None) == (self.execution is not None):
            raise ValueError("Provide exactly one of run_id or execution.")
        return self

    @model_validator(mode="after")
    def _scoring_covers_evaluator(self) -> "EvaluationRequest":
        from app.evaluation.scoring import validate_scoring_coverage

        validate_scoring_coverage(
            [rule.id for rule in self.evaluator.rules], self.scoring
        )
        return self


class EvaluationSuiteTarget(BaseModel):
    """One suite target on the wire (Phase 3G).

    Same per-target XOR contract as ``EvaluationRequest``: exactly one of
    ``run_id`` (an existing PromptRun) or ``execution`` (a fresh inline execution,
    reusing the PromptTestRequest shape) may be supplied.
    """

    run_id: UUID | None = None
    execution: PromptTestRequest | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "EvaluationSuiteTarget":
        if (self.run_id is None) == (self.execution is None):
            raise ValueError("Provide exactly one of run_id or execution.")
        return self


class EvaluationSuiteRequest(BaseModel):
    """Request to apply ONE evaluator to MULTIPLE targets (Phase 3G).

    Bounded to 1..``MAX_SUITE_TARGETS`` targets. Duplicate targets — the same
    ``run_id`` twice, or byte-identical executions — are rejected here (422) so
    the service (which re-checks defensively) and the API agree on the contract.
    """

    evaluator: EvaluatorConfig
    targets: list[EvaluationSuiteTarget] = Field(
        min_length=1, max_length=MAX_SUITE_TARGETS
    )

    @model_validator(mode="after")
    def _no_duplicate_targets(self) -> "EvaluationSuiteRequest":
        seen_run_ids: set[str] = set()
        seen_executions: set[str] = set()
        for target in self.targets:
            if target.run_id is not None:
                key = str(target.run_id)
                if key in seen_run_ids:
                    raise ValueError("Duplicate run_id in suite targets.")
                seen_run_ids.add(key)
            elif target.execution is not None:
                key = json.dumps(
                    target.execution.model_dump(mode="json"), sort_keys=True
                )
                if key in seen_executions:
                    raise ValueError("Duplicate execution in suite targets.")
                seen_executions.add(key)
        return self


class EvaluationHistoryItem(BaseModel):
    """One saved evaluation in a prompt's history (Phase 3P, list shape).

    Deliberately evidence-free: a list row locates history, it never re-states it.
    Snapshots, verdicts, and execution detail live on the detail endpoint only, so
    a bounded list stays a bounded list. ``version_number`` is read from the
    immutable PromptVersion row at query time, never copied onto the record, and
    ``scoring_mode`` is the stored mode — how that row's score was derived — not
    the caller's current preference.
    """

    evaluation_id: UUID
    prompt_version_id: UUID | None = Field(
        default=None,
        description="Exact version evaluated; null for a versionless (legacy) run.",
    )
    version_number: int | None = Field(
        default=None,
        description="Server-stored version number, joined at read time; null when the "
        "evaluation is not version-scoped.",
    )
    prompt_run_id: UUID = Field(description="The PromptRun this evaluation judged.")
    score: float | None = Field(default=None, ge=0, le=100)
    passed: bool
    scoring_mode: ScoringMode
    provider: str | None = None
    model: str | None = None
    created_at: datetime


class EvaluationRecordRead(BaseModel):
    """The complete immutable history of ONE evaluation (Phase 3P, detail shape).

    Everything needed to interpret a historical evaluation exactly as it was
    produced: the evaluator and scoring snapshots by value, the verdicts, the
    stored score (never recomputed here), and the run's execution metadata read
    from the immutable PromptRun. The current prompt body, current rules, and
    current weights are deliberately absent — this describes what happened then.
    """

    evaluation_id: UUID
    prompt_id: UUID
    prompt_version_id: UUID | None = None
    version_number: int | None = None
    prompt_run_id: UUID
    output: str = Field(description="The evaluated output, as stored on the run.")
    provider: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    usage: EvaluationUsage | None = None
    evaluator_snapshot: EvaluatorConfig
    verdicts: list[CriterionVerdict]
    passed: bool
    scoring: ScoringProfile
    score: float | None = Field(default=None, ge=0, le=100)
    created_at: datetime

    def to_evaluation_result(self) -> EvaluationResult:
        """Reuse shape (Phase 3P/26): hand this stored history to Phase 3F as a supplied result.

        The only compatibility shim: identities map onto the Phase 3E wire names
        (``prompt_run_id`` → ``run_id``, ``prompt_version_id`` → ``version_id``)
        and everything else is passed through unchanged. No provider is contacted
        and no rule is re-run; the score is re-derived from the *stored* verdicts
        under the *stored* profile, which is the same deterministic derivation the
        record was created with (asserted by tests, never a new measurement).
        """
        return EvaluationResult(
            run_id=self.prompt_run_id,
            version_id=self.prompt_version_id,
            output=self.output,
            provider=self.provider,
            model=self.model,
            latency_ms=self.latency_ms,
            usage=self.usage,
            evaluator_snapshot=self.evaluator_snapshot,
            verdicts=self.verdicts,
            passed=self.passed,
            scoring=self.scoring,
            generated_at=self.created_at,
        )