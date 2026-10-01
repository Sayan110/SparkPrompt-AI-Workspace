"""Prompt evaluation domain errors (Phase 3E)."""


class EvaluationError(Exception):
    """Base error for the prompt evaluation domain."""


class EvaluationValidationError(EvaluationError):
    """Raised when an evaluation request fails validation."""