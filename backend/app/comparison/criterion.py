"""Criterion-by-criterion comparison (Phase 3F).

Positional alignment: rule N of the left result is compared with rule N of the
right result (compatibility guarantees the same rule count and parameters when
``criterion_diffs`` is non-empty). Each row reports a neutral state:

* ``same_pass`` / ``same_fail`` — both sides agreed,
* ``left_only_pass`` / ``right_only_pass`` — one side passed while the other
  failed.

``evidence_delta`` carries only numeric evidence fields that DIFFER between the
two sides (e.g. ``{"actual_length": {"left": 44, "right": 61}}``) — raw facts,
never an interpretation of which answer is better.
"""

from __future__ import annotations

from typing import Literal

from app.comparison.types import CriterionDiff, CriterionLabel, CriterionSide
from app.evaluation.types import EvaluationResult

_STATE: dict[tuple[bool, bool], Literal["same_pass", "same_fail", "left_only_pass", "right_only_pass"]] = {
    (True, True): "same_pass",
    (False, False): "same_fail",
    (True, False): "left_only_pass",
    (False, True): "right_only_pass",
}


def _evidence_delta(left: dict, right: dict) -> dict | None:
    """Numeric evidence fields that differ between the two sides, or None."""
    delta: dict = {}
    for key, left_value in left.items():
        right_value = right.get(key)
        if (
            key in right
            and isinstance(left_value, int)
            and isinstance(right_value, int)
            and left_value != right_value
        ):
            delta[key] = {"left": left_value, "right": right_value}
    return delta or None


def compare_criteria(left: EvaluationResult, right: EvaluationResult) -> list[CriterionDiff]:
    """Compare verdicts positionally and return one neutral row per rule."""
    rows: list[CriterionDiff] = []
    for index, (left_verdict, right_verdict) in enumerate(
        zip(left.verdicts, right.verdicts)
    ):
        rows.append(
            CriterionDiff(
                rule_index=index,
                type=left_verdict.type,
                label=CriterionLabel(left=left_verdict.label, right=right_verdict.label),
                state=_STATE[(left_verdict.passed, right_verdict.passed)],
                left=CriterionSide(
                    passed=left_verdict.passed, evidence=left_verdict.evidence
                ),
                right=CriterionSide(
                    passed=right_verdict.passed, evidence=right_verdict.evidence
                ),
                evidence_delta=_evidence_delta(
                    left_verdict.evidence, right_verdict.evidence
                ),
            )
        )
    return rows