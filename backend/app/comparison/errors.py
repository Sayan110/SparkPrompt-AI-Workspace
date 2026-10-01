"""Prompt comparison domain errors (Phase 3F).

Comparison is stateless, factual, and deterministic: it describes how two
already-produced evaluation results differ — never which is better. Errors here
mirror the evaluation domain: a validation error for malformed targets and a
generic base error so the API layer can sanitize unexpected failures.
"""


class ComparisonError(Exception):
    """Base error for the prompt comparison domain."""


class ComparisonValidationError(ComparisonError):
    """Raised when a comparison request fails validation (unknown mode, partial target)."""