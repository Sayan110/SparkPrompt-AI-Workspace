"""Experiment wire schema (Phase 3H).

The request is intentionally the smallest useful surface: WHICH owned prompt, and WHAT
evaluator. The version axis is never supplied by the client — the server reads every
persisted ``PromptVersion`` of the prompt, so a caller cannot under-report or reorder
the measurement. ``evaluator`` is the existing Phase 3E ``EvaluatorConfig``, so all rule
validation (1-20 rules, per-type required fields, bounded regex, expected_output
requirements) and its 422 envelope are reused unchanged.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.evaluation.types import EvaluatorConfig


class ExperimentRequest(BaseModel):
    """Measure one evaluator across every saved version of one owned prompt.

    ``prompt_id`` must resolve to a prompt in the caller's workspace; unknown and
    foreign prompts are indistinguishable (404). The response is an
    :class:`~app.experiments.types.ExperimentResult` whose ``evaluations`` are the
    existing ``EvaluationResult`` objects, ordered by ascending ``version_number``.
    """

    prompt_id: UUID
    evaluator: EvaluatorConfig
