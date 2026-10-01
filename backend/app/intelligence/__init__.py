"""Prompt intelligence domain (Phase 3A).

Two endpoint features make up the intelligence surface: analyze (shipped Phase 3B),
and enhance + create (shipped Phase 3C).

Every real AI call goes through ``PromptIntelligenceService`` -> ``AIGateway`` ->
``ModelRouter`` -> provider adapter. Nothing in this package instantiates or talks to
a provider directly, no fabricated results are ever returned, and all model responses
are strictly parsed into the normalized contracts in :mod:`.types`.
"""

from app.intelligence.errors import (
    IntelligenceError,
    IntelligenceNotImplementedError,
    IntelligenceResponseError,
    IntelligenceValidationError,
)
from app.intelligence.prompts import IntelligenceCapability, system_prompt
from app.intelligence.service import PromptIntelligenceService, validate_prompt
from app.intelligence.types import PromptAnalysis, PromptCreation, PromptEnhancement

__all__ = [
    "IntelligenceError",
    "IntelligenceValidationError",
    "IntelligenceNotImplementedError",
    "IntelligenceResponseError",
    "IntelligenceCapability",
    "system_prompt",
    "PromptIntelligenceService",
    "validate_prompt",
    "PromptAnalysis",
    "PromptEnhancement",
    "PromptCreation",
]