"""Prompt testing domain (Phase 3D).

Runs a prompt against the existing AI gateway and returns the raw execution result —
execute + observe, with no scoring or evaluation. Every real AI call goes through
``PromptTestingService`` -> ``AIGateway`` -> ``ModelRouter`` -> provider adapter.
Nothing in this package instantiates or talks to a provider directly, no API keys are
accessed, and PromptRun persistence reuses the gateway's existing ``metadata``
mechanism — no test-specific tables exist.
"""

from app.testing.errors import (
    PromptTestingError,
    PromptTestingValidationError,
)
from app.testing.service import (
    MAX_INPUT_LENGTH,
    MAX_PROMPT_LENGTH,
    PromptTestingService,
    validate_prompt_test,
)
from app.testing.types import PromptTestResult, PromptTestUsage

__all__ = [
    "PromptTestingError",
    "PromptTestingValidationError",
    "PromptTestingService",
    "validate_prompt_test",
    "MAX_PROMPT_LENGTH",
    "MAX_INPUT_LENGTH",
    "PromptTestResult",
    "PromptTestUsage",
]