"""Prompt experiment domain errors (Phase 3H).

Experiments are stateless measurements over already-persisted prompt versions: they
read owned versions, run each one through the existing evaluation path, and report
counts. The error surface mirrors the Phase 3E/3F/3G domains — a validation error for
a request the service refuses, and a generic base error so the API layer can sanitize
unexpected failures.
"""


class ExperimentError(Exception):
    """Base error for the prompt experiment domain."""


class ExperimentValidationError(ExperimentError):
    """Raised when an experiment request fails service-level validation.

    Covers conditions the wire schema cannot know in advance — an unknown/foreign
    prompt surfaces as ``NotFoundError`` instead (ownership-first 404), while a version
    count above ``MAX_EXPERIMENT_VERSIONS`` or a stored version body that cannot be
    executed surfaces here as a clean 400.
    """
