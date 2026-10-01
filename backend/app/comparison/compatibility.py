"""Structural evaluator compatibility (Phase 3F).

Two evaluator snapshots are comparable iff, after defaults are materialized
(reading the serialized ``model_dump`` of each ``Rule``), they have:

* the same rule count, and
* the same ``type`` and the same semantic parameters (``text``, ``length``,
  ``pattern``, ``case_sensitive``, ``strip``) at every position, and
* the same ``expected_output`` at the evaluator level.

Alignment is positional: rule ids, labels, and the evaluator name are cosmetic
and never affect comparability. When snapshots differ, the returned
:class:`~app.comparison.types.EvaluatorCompatibility` lists the concrete
differences (``rule_index``, ``field``, both values) and ``criterion_diffs`` is
empty — the caller still gets output / prompt / metadata differences.

This module is AI-free: it imports only the comparison contracts and the
AI-free evaluation rule model.
"""

from __future__ import annotations

from app.comparison.types import EvaluatorCompatibility, FieldMismatch
from app.evaluation.types import EvaluatorConfig, Rule

# Semantic parameters compared per position, in a stable order. These are the
# only fields that affect how a rule is executed by the Phase 3E checkers.
_SEMANTIC_FIELDS = ("type", "text", "length", "pattern", "case_sensitive", "strip")
_ROOT_FIELD = -1  # rule_index sentinel for whole-evaluator differences.


def _signature(rule: Rule) -> dict:
    """Serialize one rule's semantic parameters (defaults already materialized)."""
    return {field: getattr(rule, field) for field in _SEMANTIC_FIELDS}


def compare_evaluators(
    left: EvaluatorConfig, right: EvaluatorConfig
) -> EvaluatorCompatibility:
    """Return whether ``left`` and ``right`` can be compared criterion-by-criterion."""
    mismatches: list[FieldMismatch] = []

    if len(left.rules) != len(right.rules):
        mismatches.append(
            FieldMismatch(
                rule_index=_ROOT_FIELD,
                field="rule_count",
                left_value=len(left.rules),
                right_value=len(right.rules),
            )
        )
        return EvaluatorCompatibility(
            comparable=False, status="incompatible", mismatches=mismatches
        )

    for index, (left_rule, right_rule) in enumerate(zip(left.rules, right.rules)):
        left_sig = _signature(left_rule)
        right_sig = _signature(right_rule)
        for field in _SEMANTIC_FIELDS:
            if left_sig[field] != right_sig[field]:
                mismatches.append(
                    FieldMismatch(
                        rule_index=index,
                        field=field,
                        left_value=left_sig[field],
                        right_value=right_sig[field],
                    )
                )

    if left.expected_output != right.expected_output:
        mismatches.append(
            FieldMismatch(
                rule_index=_ROOT_FIELD,
                field="expected_output",
                left_value=left.expected_output,
                right_value=right.expected_output,
            )
        )

    if mismatches:
        return EvaluatorCompatibility(
            comparable=False, status="incompatible", mismatches=mismatches
        )
    return EvaluatorCompatibility(comparable=True, status="identical", mismatches=[])