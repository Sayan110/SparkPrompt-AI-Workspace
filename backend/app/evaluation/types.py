"""Normalized prompt-evaluation contracts (Phase 3E).

Phase 3E is a stateless, deterministic evaluation foundation: evaluation consumes an
existing PromptRun or a fresh PromptTestingService execution and applies explicit
user-authored rules to the generated output. Every contract here is AI-free — no
provider, gateway, router, or registry is imported — and the result carries raw
PASS/FAIL verdicts with bounded evidence plus a score derived from those verdicts
(Phase 3K; no LLM-as-judge, no comparison). Phase 3P adds one optional identity,
``evaluation_id``: the id of the durable historical record the API saved the finished
result under. Persistence itself lives in the service/route layer — the contracts here
still never touch the database, and a result without an ``evaluation_id`` is simply one
nothing was saved for.

The ``Rule``/``EvaluatorConfig`` models own schema-level validation (rule count 1-20,
per-type required fields, bounded regex, expected_output requirements), so malformed
rules fail fast with the project's normal Pydantic 422 envelope at the API boundary.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, FiniteFloat, field_validator, model_validator

from app.testing.service import MAX_INPUT_LENGTH, MAX_PROMPT_LENGTH

from app.evaluation.scoring import (
    MAX_WEIGHT,
    score_evaluation,
    validate_scoring_coverage,
)

MAX_RULES = 20
"""Upper bound for the number of rules in one evaluator (no zero-rule requests)."""
MAX_REGEX_LENGTH = 500
"""Upper bound for a user-supplied regex pattern (payload / ReDoS cap)."""
MAX_TEXT_LENGTH = 5_000
"""Upper bound for a contains / not_contains search string."""
MAX_EXPECTED_OUTPUT_LENGTH = 20_000
"""Upper bound for the optional expected_output (golden answer)."""

RuleType = Literal[
    "contains",
    "not_contains",
    "min_length",
    "max_length",
    "exact_match",
    "normalized_match",
    "regex_match",
]


class Rule(BaseModel):
    """A single deterministic evaluation rule.

    Only the fields relevant to the rule's ``type`` are required; irrelevant fields are
    ignored. Unknown ``type`` values fail the Literal validation. Regex patterns are
    compiled here at validation time so a malformed pattern never reaches evaluation.
    User code is never executed: patterns are only ever compiled and searched.
    """

    id: str | None = Field(default=None, max_length=120)
    label: str | None = Field(default=None, max_length=200)
    type: RuleType
    text: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    length: int | None = Field(default=None, ge=1, le=1_000_000)
    pattern: str | None = Field(default=None, max_length=MAX_REGEX_LENGTH)
    case_sensitive: bool = True
    strip: bool = False

    @model_validator(mode="after")
    def _validate_rule_fields(self) -> "Rule":
        if self.type in ("contains", "not_contains"):
            if not (self.text or "").strip():
                raise ValueError(f"{self.type} requires a non-blank text to search for.")
        elif self.type in ("min_length", "max_length"):
            if self.length is None:
                raise ValueError(f"{self.type} requires a length.")
        elif self.type == "regex_match":
            pattern = self.pattern or ""
            if not pattern.strip():
                raise ValueError("regex_match requires a pattern.")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"regex_match pattern is invalid: {exc}") from None
        return self


class EvaluatorConfig(BaseModel):
    """The evaluator specification, exactly as executed.

    Rule count is bounded to 1-20. When a rule uses ``expected_output``
    (``exact_match`` / ``normalized_match``), the expected output is required.
    """

    name: str | None = Field(default=None, max_length=120)
    rules: list[Rule] = Field(min_length=1, max_length=MAX_RULES)
    expected_output: str | None = Field(
        default=None, max_length=MAX_EXPECTED_OUTPUT_LENGTH
    )

    @model_validator(mode="after")
    def _expected_output_required_for_match_rules(self) -> "EvaluatorConfig":
        uses_expected = any(
            rule.type in ("exact_match", "normalized_match") for rule in self.rules
        )
        if uses_expected and not (self.expected_output or "").strip():
            raise ValueError(
                "expected_output is required when a rule uses exact_match or "
                "normalized_match."
            )
        return self


class EvaluationExecution(BaseModel):
    """MODE B input: parameters for a fresh inline execution.

    Mirrors ``PromptTestRequest``'s shape so the wire contract can reuse it directly;
    the limits are imported from the testing service (the single source of truth).
    ``version_id`` carries a server-resolved version execution (e.g. experiments);
    it is never caller-asserted identity — the testing layer re-resolves it.
    """

    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)
    test_input: str | None = Field(default=None, max_length=MAX_INPUT_LENGTH)
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=120)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1, le=262_144)
    prompt_id: UUID | None = None
    version_id: UUID | None = None

    @field_validator("prompt")
    @classmethod
    def _prompt_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value


class CriterionVerdict(BaseModel):
    """PASS/FAIL for one rule, with bounded evidence (never the full output)."""

    rule_id: str | None = None
    label: str | None = None
    type: RuleType
    passed: bool
    evidence: dict = Field(
        default_factory=dict,
        description="Bounded, per-type evidence (matched/text/lengths) — never the output.",
    )


ScoringMode = Literal["unweighted", "weighted"]


class RuleWeight(BaseModel):
    """One rule's contribution to a weighted score (Phase 3L).

    Weights are keyed by the rule's existing stable ``Rule.id`` — never by
    position, label, or type. A weight never changes whether its rule passes
    or fails; it only scales that verdict's share of the score.
    """

    rule_id: str = Field(min_length=1, max_length=120)
    weight: FiniteFloat = Field(
        gt=0,
        le=MAX_WEIGHT,
        description="Positive finite contribution of this rule to a weighted score.",
    )


class ScoringProfile(BaseModel):
    """Explicit deterministic scoring configuration (Phase 3L).

    Local to one evaluation request: ``mode`` selects unweighted (Phase 3K
    behavior, weights ignored entirely) or weighted aggregation. Weights are a
    list of ``{rule_id, weight}`` pairs — a list rather than a map so duplicate
    definitions stay detectable and rejectable instead of collapsing silently
    in JSON parsing. Coverage (every rule weighted exactly once, no unknown
    ids) is enforced where the evaluator rules are known, never assumed.
    """

    mode: ScoringMode = Field(
        default="unweighted",
        description="unweighted: one vote per rule (Phase 3K). weighted: votes scaled by weight.",
    )
    weights: list[RuleWeight] = Field(
        default_factory=list,
        description="Per-rule weights, used only when mode is weighted.",
    )

    @model_validator(mode="after")
    def _no_duplicate_weights(self) -> "ScoringProfile":
        seen: set[str] = set()
        for entry in self.weights:
            if entry.rule_id in seen:
                raise ValueError(f"duplicate weight for rule id {entry.rule_id!r}.")
            seen.add(entry.rule_id)
        return self


def default_scoring_profile() -> ScoringProfile:
    """The Phase 3K behavior: unweighted scoring with no weights."""
    return ScoringProfile()


class EvaluationUsage(BaseModel):
    """Normalized token usage for the evaluated execution; None = not reported."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class EvaluationResult(BaseModel):
    """The outcome of a deterministic evaluation.

    ``evaluator_snapshot`` is the evaluator config exactly as executed; ``passed`` is
    the AND of every criterion verdict. ``score`` is derived from those same verdicts
    (Phase 3K): the deterministic percentage of evaluator criteria passed, or null
    when there are no verdicts. It is descriptive metadata only — not a quality,
    intelligence, model, or success-probability judgment.
    """

    run_id: UUID | None = Field(
        default=None,
        description="PromptRun id; set when evaluating an existing run or when a fresh "
        "execution was persisted via prompt_id.",
    )
    version_id: UUID | None = Field(
        default=None,
        description="Exact PromptVersion executed: the run's stored version identity "
        "(MODE A) or the requested version execution (MODE B). Null means the "
        "execution was not version-scoped. Read-only metadata, never evaluated.",
    )
    evaluation_id: UUID | None = Field(
        default=None,
        description="Id of the durable EvaluationRecord this evaluation was saved as "
        "(Phase 3P), set by POST /api/evaluations/run when the evaluation could be "
        "anchored to a persisted PromptRun. Null means nothing was saved: a draft "
        "execution with no persisted run, or a suite / experiment / comparison "
        "evaluation, which Phase 3P deliberately does not persist. This is the "
        "historical record id — never confused with run_id, the evaluated PromptRun.",
    )
    output: str = Field(description="The evaluated output.")
    provider: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    usage: EvaluationUsage | None = None
    evaluator_snapshot: EvaluatorConfig = Field(
        description="The evaluator exactly as executed (defaults included)."
    )
    verdicts: list[CriterionVerdict]
    passed: bool = Field(description="True when every criterion passed (AND).")
    scoring: ScoringProfile = Field(
        default_factory=default_scoring_profile,
        description="Active scoring profile: how the score below was derived.",
    )
    score: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description=(
            "Deterministic percentage of evaluator criteria passed "
            "(round(passed/total*100, 2), or the weighted equivalent), "
            "or null when there are no verdicts."
        ),
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _apply_scoring_profile(self) -> "EvaluationResult":
        """Validate coverage first, then derive the score — one definition.

        Validators run whether the result comes from the service or is supplied
        by a caller, so a caller-provided score can never survive: it is always
        recomputed from the verdicts under the result's own profile. Verdicts
        and ``passed`` are left untouched.
        """
        validate_scoring_coverage(
            [rule.id for rule in self.evaluator_snapshot.rules], self.scoring
        )
        self.score = score_evaluation(self.verdicts, self.scoring)
        return self