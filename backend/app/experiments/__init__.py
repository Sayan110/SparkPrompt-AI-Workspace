"""Prompt experiment domain (Phase 3H).

A stateless, factual sibling of the Phase 3E evaluation / 3G suite domains: given ONE
owned prompt, apply ONE ``EvaluatorConfig`` to EVERY persisted ``PromptVersion`` as a
fresh execution and report integer counts plus each per-version ``EvaluationResult``
unchanged, in ascending ``version_number`` order.

The experiment measures; it never judges. No aggregate score, percentage, ratio,
grade, ranking, winner, or recommendation exists here, and the per-version payload
is the existing Phase 3E ``EvaluationResult`` (which carries its own derived score
as descriptive metadata) rather than a second structure. Nothing is persisted, no
version is modified, and no history is kept.

Importing this package must NOT pull in the AI stack (provider adapters, gateway,
router, registry): the service composes over the AI-free evaluation contracts and
resolves the owned-prompt accessor and the evaluation service lazily.
"""

from app.evaluation.service import EvaluationService
from app.evaluation.types import EvaluationResult, EvaluatorConfig
from app.experiments.errors import ExperimentError, ExperimentValidationError
from app.experiments.service import ExperimentService
from app.experiments.types import MAX_EXPERIMENT_VERSIONS, ExperimentResult

__all__ = [
    "MAX_EXPERIMENT_VERSIONS",
    "EvaluationResult",
    "EvaluationService",
    "EvaluatorConfig",
    "ExperimentError",
    "ExperimentResult",
    "ExperimentService",
    "ExperimentValidationError",
]
