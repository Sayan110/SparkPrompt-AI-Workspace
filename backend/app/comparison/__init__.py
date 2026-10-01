"""Prompt comparison foundation (Phase 3F).

A stateless, factual, deterministic sibling of the Phase 3E evaluation domain:
given two already-produced evaluation results, describe how they differ —
positional criterion states, bounded output/prompt diffs, metadata differences,
and structural evaluator compatibility. Never a verdict about which answer is
better: no winner/loser, no ranking, no recommendation. (Per-side deterministic
scores and their factual delta are carried as metadata only, never as a verdict.)

Importing this package must NOT pull in the AI stack (provider/gateway/router/
registry): the pure modules import only stdlib + ``app.comparison`` + the
AI-free evaluation contracts, and the service lazily imports the run accessor
and evaluation service.
"""

from app.comparison.compatibility import compare_evaluators
from app.comparison.criterion import compare_criteria
from app.comparison.diff import line_diff
from app.comparison.errors import ComparisonError, ComparisonValidationError
from app.comparison.metadata import compare_metadata
from app.comparison.service import (
    ComparisonService,
    build_comparison,
    build_prompt_diff,
    extract_executed_prompt,
)
from app.comparison.types import (
    MAX_DIFF_CHARS,
    MAX_DIFF_HUNKS,
    MAX_DIFF_LINE_LENGTH,
    MAX_OUTPUT_DISPLAY,
    MAX_SUPPLIED_OUTPUT,
    ComparisonResult,
    CriterionDiff,
    CriterionLabel,
    CriterionSide,
    DiffLine,
    EvaluatorCompatibility,
    ExecutionOutput,
    ExecutionSummary,
    FieldDiff,
    FieldMismatch,
    MetadataDiff,
    OutputSummary,
    PromptDiff,
    UsageDiff,
    UsageFieldDiff,
)

__all__ = [
    "MAX_DIFF_CHARS",
    "MAX_DIFF_HUNKS",
    "MAX_DIFF_LINE_LENGTH",
    "MAX_OUTPUT_DISPLAY",
    "MAX_SUPPLIED_OUTPUT",
    "ComparisonError",
    "ComparisonService",
    "ComparisonValidationError",
    "ComparisonResult",
    "CriterionDiff",
    "CriterionLabel",
    "CriterionSide",
    "DiffLine",
    "EvaluatorCompatibility",
    "ExecutionOutput",
    "ExecutionSummary",
    "FieldDiff",
    "FieldMismatch",
    "MetadataDiff",
    "OutputSummary",
    "PromptDiff",
    "UsageDiff",
    "UsageFieldDiff",
    "build_comparison",
    "build_prompt_diff",
    "compare_criteria",
    "compare_evaluators",
    "compare_metadata",
    "extract_executed_prompt",
    "line_diff",
]