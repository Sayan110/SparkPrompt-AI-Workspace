"""Deterministic evaluation scoring (Phases 3K and 3L).

The score is DERIVED from existing Phase 3E verdicts — it never changes how any
evaluator works. Two modes, selected by an explicit scoring profile:

* **unweighted** (default): ``round(passed / total * 100, 2)``.
* **weighted**: each rule contributes its positive weight instead of one vote::

      round(passed_weight / total_weight * 100, 2)

A weight never changes whether a rule passes or fails; it only affects
aggregation. A ``None`` score means there is no measurable denominator (no
verdicts, or — defensively — no positive total weight), never zero.

Purity contract (enforced by tests): this module imports only the standard
library. It never calls AI providers, never touches the database, never
executes prompts, and never modifies verdicts or evaluator configuration.
Dependency direction stays::

    AI execution -> Evaluation -> Scoring

Verdicts and scoring profiles are consumed duck-typed (objects with attributes
or mappings with keys) so the helpers are testable without PostgreSQL,
FastAPI, providers, or the network — and so this module never imports the
Pydantic contracts (which would pull in the testing service).
"""

from __future__ import annotations

from typing import Any, Sequence

SCORE_MIN = 0
"""Lowest possible score: no criterion passed."""

SCORE_MAX = 100
"""Highest possible score: every criterion passed."""

SCORE_PRECISION = 2
"""Decimal places kept in the serialized score (stable, artifact-free)."""

MAX_WEIGHT = 1000
"""Upper bound for one rule weight (mirrors the bounded-limits style of the
evaluator contract: ample relative spread, no pathological magnitudes)."""


def _passed_of(verdict: Any) -> bool:
    """Read the ``passed`` flag from a verdict object or mapping."""
    if isinstance(verdict, dict):
        return bool(verdict.get("passed", False))
    return bool(getattr(verdict, "passed", False))


def _rule_id_of(verdict: Any) -> str | None:
    """Read the rule id from a verdict object or mapping (None when absent)."""
    if isinstance(verdict, dict):
        value = verdict.get("rule_id")
    else:
        value = getattr(verdict, "rule_id", None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _profile_mode(scoring: Any) -> str:
    """Read the profile mode, defaulting to unweighted for a missing profile."""
    if scoring is None:
        return "unweighted"
    if isinstance(scoring, dict):
        return str(scoring.get("mode", "unweighted"))
    return str(getattr(scoring, "mode", "unweighted"))


def _profile_weights(scoring: Any) -> list[tuple[str, float]]:
    """Read ``[(rule_id, weight)]`` pairs from a profile object or mapping."""
    if scoring is None:
        return []
    weights = scoring.get("weights", []) if isinstance(scoring, dict) else getattr(scoring, "weights", [])
    pairs: list[tuple[str, float]] = []
    for entry in weights or []:
        if isinstance(entry, dict):
            rule_id, weight = entry.get("rule_id"), entry.get("weight")
        else:
            rule_id, weight = getattr(entry, "rule_id", None), getattr(entry, "weight", None)
        if rule_id is None or weight is None:
            continue
        pairs.append((str(rule_id), float(weight)))
    return pairs


def validate_scoring_coverage(rule_ids: Sequence[Any] | None, scoring: Any = None) -> None:
    """Enforce the strict weighted-mode coverage policy (pure, no I/O).

    Unweighted mode ignores weights entirely and always passes. Weighted mode
    requires: every rule carries a non-blank id, rule ids are unique, every
    rule has exactly one weight, and no weight names an unknown rule id.
    Raises ``ValueError`` on any violation (Pydantic and service layers turn
    this into 422 / 400 respectively, always before provider contact).
    """
    if _profile_mode(scoring) != "weighted":
        return
    ids = [(None if value is None else str(value).strip() or None) for value in (rule_ids or [])]
    if any(rule_id is None for rule_id in ids):
        raise ValueError("weighted scoring requires every rule to have an id.")
    if len(set(ids)) != len(ids):
        raise ValueError("weighted scoring requires unique rule ids.")
    pairs = _profile_weights(scoring)
    wanted = set(ids)
    seen: set[str] = set()
    for rule_id, _ in pairs:
        if rule_id in seen:
            raise ValueError(f"duplicate weight for rule id {rule_id!r}.")
        seen.add(rule_id)
    unknown = seen - wanted
    if unknown:
        raise ValueError(f"weights name unknown rule ids: {sorted(unknown)}.")
    missing = wanted - seen
    if missing:
        raise ValueError(f"rules missing weights: {sorted(missing)}.")


def score_evaluation(verdicts: Sequence[Any] | None, scoring: Any = None) -> float | None:
    """Derive a deterministic percentage score from criterion verdicts.

    Unweighted (default): ``round(passed / total * 100, 2)``. Weighted: each
    verdict contributes its rule's weight, ``round(passed_weight /
    total_weight * 100, 2)``. Returns ``None`` when there is no measurable
    denominator. The result always satisfies ``0 <= score <= 100``; NaN and
    Infinity are impossible by construction (finite positive weights, guarded
    division).

    Verdicts whose rule id has no weight are skipped (neither numerator nor
    denominator). Validated callers never produce them — coverage is enforced
    upstream — so this is a defensive fallback, not a silent default.
    """
    verdicts = list(verdicts or [])
    if not verdicts:
        return None
    if _profile_mode(scoring) != "weighted":
        passed = sum(1 for verdict in verdicts if _passed_of(verdict))
        return round(passed / len(verdicts) * SCORE_MAX, SCORE_PRECISION)
    weight_of = dict(_profile_weights(scoring))
    passed_weight = 0.0
    total_weight = 0.0
    for verdict in verdicts:
        weight = weight_of.get(_rule_id_of(verdict) or "")
        if weight is None:
            continue
        total_weight += weight
        if _passed_of(verdict):
            passed_weight += weight
    if total_weight <= 0:
        return None
    return round(passed_weight / total_weight * SCORE_MAX, SCORE_PRECISION)
