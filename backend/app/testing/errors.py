class PromptTestingError(Exception):
    """Base error for the prompt testing domain (Phase 3D)."""


class PromptTestingValidationError(PromptTestingError):
    """Raised when a prompt test request fails validation."""