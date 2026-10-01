from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, model_validator

from app.comparison.types import MAX_SUPPLIED_OUTPUT
from app.evaluation.types import EvaluationResult, EvaluatorConfig


class ComparisonRequest(BaseModel):
    """Request to compare two already-produced evaluation results (Phase 3F).

    Exactly ONE target must be supplied:

    * MODE A (run-pair): ``left_run_id`` + ``right_run_id`` + ``evaluator`` —
      both owned runs are re-evaluated with the SAME evaluator via the existing
      evaluation service (no provider contact; run-pair mode never needs one).
    * MODE B (supplied): ``left_evaluation`` + ``right_evaluation`` — two
      already-produced results compared directly (no DB, no provider).

    Invalid combinations — both modes, neither mode, or a partial target — fail
    FastAPI validation (422) before anything runs. Supplied outputs larger than
    :data:`MAX_SUPPLIED_OUTPUT` are rejected here as well, bounding the work the
    comparison service is allowed to do on client-supplied text.
    """

    left_run_id: UUID | None = None
    right_run_id: UUID | None = None
    evaluator: EvaluatorConfig | None = None
    left_evaluation: EvaluationResult | None = None
    right_evaluation: EvaluationResult | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "ComparisonRequest":
        run_mode = (
            self.left_run_id is not None
            or self.right_run_id is not None
            or self.evaluator is not None
        )
        supplied_mode = (
            self.left_evaluation is not None or self.right_evaluation is not None
        )
        if run_mode == supplied_mode:
            raise ValueError(
                "Provide exactly one comparison target: run-pair "
                "(left_run_id, right_run_id, evaluator) or supplied "
                "(left_evaluation, right_evaluation)."
            )
        if run_mode:
            if (
                self.left_run_id is None
                or self.right_run_id is None
                or self.evaluator is None
            ):
                raise ValueError(
                    "Run-pair comparison requires left_run_id, right_run_id, and "
                    "evaluator together."
                )
        else:
            if self.left_evaluation is None or self.right_evaluation is None:
                raise ValueError(
                    "Supplied comparison requires both left_evaluation and "
                    "right_evaluation."
                )
            if len(self.left_evaluation.output) > MAX_SUPPLIED_OUTPUT:
                raise ValueError(
                    f"Supplied output is too large (max {MAX_SUPPLIED_OUTPUT} characters)."
                )
            if len(self.right_evaluation.output) > MAX_SUPPLIED_OUTPUT:
                raise ValueError(
                    f"Supplied output is too large (max {MAX_SUPPLIED_OUTPUT} characters)."
                )
        return self