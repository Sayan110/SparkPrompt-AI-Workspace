"""Deterministic, AI-free evaluation checkers (Phase 3E).

Each checker is a pure function over ``(output, rule, expected_output)``: no AI calls,
no provider/gateway/router/registry imports, no I/O, no ``eval()``, and no execution of
user code.

Security posture:

* Regex patterns are bounded (max 500 chars) and compiled by the ``Rule`` contract at
  validation time; ``regex_match`` compiles once more per evaluation before searching.
* Evidence is bounded — it carries only matched flags, the (bounded) search text, and
  lengths. It never contains the generated output.
* A rule that somehow reaches this layer invalid (unsupported type, unreadable
  pattern, missing expected_output) is treated as a validation error, never executed.

The composer ``evaluate()`` returns one :class:`CriterionVerdict` per rule; the
aggregate PASS/FAIL (AND) is computed by the service layer.
"""

from __future__ import annotations

import re

from app.evaluation.errors import EvaluationValidationError
from app.evaluation.types import CriterionVerdict, Rule


def _normalize(text: str) -> str:
    """strip + casefold + collapse internal whitespace (for normalized_match)."""
    return " ".join(text.strip().casefold().split())


def contains(output: str, rule: Rule, expected_output: str | None = None) -> tuple[bool, dict]:
    """Pass when ``rule.text`` occurs in ``output``."""
    del expected_output
    text = rule.text or ""
    if rule.case_sensitive:
        found = text in output
    else:
        found = text.casefold() in output.casefold()
    return found, {"matched": found, "text": text}


def not_contains(output: str, rule: Rule, expected_output: str | None = None) -> tuple[bool, dict]:
    """Pass when ``rule.text`` does NOT occur in ``output``."""
    del expected_output
    text = rule.text or ""
    if rule.case_sensitive:
        found = text in output
    else:
        found = text.casefold() in output.casefold()
    return not found, {"matched": found, "text": text}


def min_length(output: str, rule: Rule, expected_output: str | None = None) -> tuple[bool, dict]:
    """Pass when ``len(output) >= rule.length`` (optional strip applied first)."""
    del expected_output
    text = output.strip() if rule.strip else output
    length = rule.length or 0
    return len(text) >= length, {"actual_length": len(text), "minimum": length}


def max_length(output: str, rule: Rule, expected_output: str | None = None) -> tuple[bool, dict]:
    """Pass when ``len(output) <= rule.length`` (optional strip applied first)."""
    del expected_output
    text = output.strip() if rule.strip else output
    length = rule.length or 0
    return len(text) <= length, {"actual_length": len(text), "maximum": length}


def exact_match(output: str, rule: Rule, expected_output: str | None) -> tuple[bool, dict]:
    """Pass when ``output == expected_output``."""
    del rule
    if expected_output is None:
        raise EvaluationValidationError("exact_match requires expected_output.")
    matched = output == expected_output
    return matched, {
        "matched": matched,
        "expected_length": len(expected_output),
        "actual_length": len(output),
    }


def normalized_match(output: str, rule: Rule, expected_output: str | None) -> tuple[bool, dict]:
    """Pass when both sides match after strip + casefold + whitespace collapse."""
    del rule
    if expected_output is None:
        raise EvaluationValidationError("normalized_match requires expected_output.")
    matched = _normalize(output) == _normalize(expected_output)
    return matched, {"matched": matched}


def regex_match(output: str, rule: Rule, expected_output: str | None = None) -> tuple[bool, dict]:
    """Pass when ``re.search(pattern, output)`` finds a match.

    Compiles the pattern once per evaluation; an invalid pattern is a validation error.
    """
    del expected_output
    pattern = rule.pattern or ""
    flags = 0 if rule.case_sensitive else re.IGNORECASE
    try:
        compiled = re.compile(pattern, flags)
    except re.error as exc:
        raise EvaluationValidationError(f"regex_match pattern is invalid: {exc}") from None
    matched = compiled.search(output) is not None
    return matched, {"matched": matched}


_CHECKERS = {
    "contains": contains,
    "not_contains": not_contains,
    "min_length": min_length,
    "max_length": max_length,
    "exact_match": exact_match,
    "normalized_match": normalized_match,
    "regex_match": regex_match,
}


def run_checker(output: str, rule: Rule, expected_output: str | None) -> CriterionVerdict:
    """Run one rule against the output and return a typed verdict."""
    checker = _CHECKERS.get(rule.type)
    if checker is None:  # Unreachable through the Literal contract; defensive only.
        raise EvaluationValidationError(f"Unsupported rule type: {rule.type}")
    passed, evidence = checker(output, rule, expected_output)
    return CriterionVerdict(
        rule_id=rule.id,
        label=rule.label,
        type=rule.type,
        passed=passed,
        evidence=evidence,
    )


def evaluate(
    output: str, rules: list[Rule], expected_output: str | None
) -> list[CriterionVerdict]:
    """Run every rule against the output and return contiguous verdicts."""
    return [run_checker(output, rule, expected_output) for rule in rules]