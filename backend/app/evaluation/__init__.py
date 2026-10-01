"""Prompt evaluation domain (Phases 3E and 3G).

Stateless, deterministic prompt evaluation foundation: evaluation consumes an existing
PromptRun or a fresh ``PromptTestingService`` execution and applies explicit
user-authored rules to the generated output, returning per-criterion PASS/FAIL verdicts
with bounded evidence and an aggregate PASS/FAIL. Phase 3G adds the evaluation suite,
which applies ONE evaluator to MULTIPLE targets (run_id and/or execution) and reports
integer counts only (``total``/``passed``) plus each per-target result unchanged. The
checker layer is completely AI-free — no provider, gateway, router, registry,
LLM-as-judge, embeddings, persistence, or comparison. (The numeric score on
``EvaluationResult`` is derived from verdicts by Phase 3K scoring, not by the
checker layer.)
"""

from app.evaluation.checkers import evaluate
from app.evaluation.errors import EvaluationError, EvaluationValidationError
from app.evaluation.scoring import score_evaluation, validate_scoring_coverage
from app.evaluation.service import EvaluationService
from app.evaluation.suite import EvaluationSuiteService
from app.evaluation.suite_types import (
    MAX_SUITE_TARGETS,
    EvaluationSuiteResult,
    EvaluationSuiteTarget,
)
from app.evaluation.types import (
    MAX_EXPECTED_OUTPUT_LENGTH,
    MAX_REGEX_LENGTH,
    MAX_RULES,
    MAX_TEXT_LENGTH,
    MAX_WEIGHT,
    CriterionVerdict,
    EvaluationExecution,
    EvaluationResult,
    EvaluationUsage,
    EvaluatorConfig,
    Rule,
    RuleType,
    RuleWeight,
    ScoringMode,
    ScoringProfile,
    default_scoring_profile,
)

__all__ = [
    "EvaluationError",
    "EvaluationValidationError",
    "EvaluationService",
    "EvaluationSuiteService",
    "EvaluationSuiteResult",
    "EvaluationSuiteTarget",
    "EvaluationExecution",
    "EvaluationResult",
    "EvaluationUsage",
    "EvaluatorConfig",
    "CriterionVerdict",
    "Rule",
    "RuleType",
    "RuleWeight",
    "ScoringMode",
    "ScoringProfile",
    "default_scoring_profile",
    "evaluate",
    "score_evaluation",
    "validate_scoring_coverage",
    "MAX_RULES",
    "MAX_WEIGHT",
    "MAX_RULES",
    "MAX_REGEX_LENGTH",
    "MAX_TEXT_LENGTH",
    "MAX_EXPECTED_OUTPUT_LENGTH",
    "MAX_SUITE_TARGETS",
]