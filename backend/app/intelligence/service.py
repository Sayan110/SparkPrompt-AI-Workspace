"""Prompt intelligence service.

Phase 3A established the service boundary, its dependency on the AI gateway
abstraction, and the normalized contracts; every capability raised a typed
not-implemented error. Phase 3B implements ``analyze()``: the prompt is validated,
routed through the injected ``AIGateway`` (which owns provider routing, error handling,
and PromptRun persistence), and the structured AI response is strictly parsed and
normalized into ``PromptAnalysis``. Phase 3C implements ``enhance()`` and ``create()``
with the same strict structured-output discipline: both share the request builder and
contract parser that analyze uses, so one parsing path governs every capability and no
copy-pasted parsing exists. All three capabilities now apply identical validation,
strict output-contract enforcement, and never surface raw model output.

The intelligence layer never instantiates or imports a provider adapter. Request and
message types are imported lazily inside the method that executes them, so importing
``app.intelligence`` alone still does not pull in the AI stack.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel, ValidationError

from app.intelligence.errors import (
    IntelligenceResponseError,
    IntelligenceValidationError,
)
from app.intelligence.prompts import IntelligenceCapability, system_prompt
from app.intelligence.types import (
    PromptAnalysis,
    PromptCreation,
    PromptEnhancement,
)

if TYPE_CHECKING:
    from app.ai.gateway import AIGateway
    from app.ai.types import AIMessage, AIRequest
    from sqlalchemy.orm import Session

MAX_PROMPT_LENGTH = 20_000
"""Upper bound for analysis/enhancement input and creation goals/context."""

_STRUCTURED_TEMPERATURE = 0.3
_STRUCTURED_MAX_TOKENS = 1024

_ANALYSIS_KEYS = (
    "clarity",
    "specificity",
    "context",
    "constraints",
    "output_format",
    "missing_information",
    "suggestions",
)
_ENHANCEMENT_KEYS = ("original_prompt", "enhanced_prompt", "improvements")
_CREATION_KEYS = ("prompt", "rationale")

_StructuredContractT = TypeVar("_StructuredContractT", bound=BaseModel)


def validate_prompt(prompt: str) -> str:
    """Validate an intelligence input and return it trimmed.

    Validation runs before any capability dispatch so malformed input fails fast and
    predictably, independent of whether the capability is implemented.
    """
    cleaned = (prompt or "").strip()
    if not cleaned:
        raise IntelligenceValidationError("Prompt must not be empty.")
    if len(cleaned) > MAX_PROMPT_LENGTH:
        raise IntelligenceValidationError(
            f"Prompt is too long ({len(cleaned)} characters; limit {MAX_PROMPT_LENGTH})."
        )
    return cleaned


def _build_structured_request(
    capability: IntelligenceCapability,
    user_content: str,
    provider: str | None,
    model: str | None,
) -> "AIRequest":
    """Build the gateway request for a structured capability.

    Shared by analyze, enhance, and create so every capability uses the same system
    prompt source, temperature, token ceiling, non-streaming mode, and metadata-less
    execution. Imported lazily to keep importing ``app.intelligence`` free of the AI
    stack (the Phase 3A provider-independence guarantee).
    """
    from app.ai.types import AIMessage, AIRequest

    return AIRequest(
        provider=provider or "",
        model=model or "",
        messages=[
            AIMessage(
                role="system",
                content=system_prompt(capability),
            ),
            AIMessage(role="user", content=user_content),
        ],
        temperature=_STRUCTURED_TEMPERATURE,
        max_tokens=_STRUCTURED_MAX_TOKENS,
        stream=False,
        metadata=None,
    )


def _extract_json(text: str) -> object:
    """Extract the first JSON value from a model response.

    Accepts a clean JSON document, a fenced code block, or a response with stray
    commentary so a robust payload can still be recovered. Returns None when nothing
    parseable exists.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
        end = stripped.find("```")
        if end != -1:
            stripped = stripped[:end]
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    while start != -1:
        end = stripped.find("}", start)
        while end != -1:
            candidate = stripped[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
            end = stripped.find("}", end + 1)
        start = stripped.find("{", start + 1)
    return None


def _parse_contract(
    response_text: str,
    capability: IntelligenceCapability,
    required_keys: tuple[str, ...],
    contract_model: type[_StructuredContractT],
) -> _StructuredContractT:
    """Strictly normalize a raw model response into a structured-output contract.

    Malformed JSON, missing or mistyped keys, out-of-bounds values, and anything that
    fails the normalized contract raise ``IntelligenceResponseError``. The raw model
    output is never surfaced to callers or the API client.
    """
    payload = _extract_json(response_text)
    if not isinstance(payload, dict):
        raise IntelligenceResponseError(capability.value)
    if any(key not in payload for key in required_keys):
        raise IntelligenceResponseError(capability.value)
    try:
        return contract_model.model_validate(payload)
    except ValidationError:
        raise IntelligenceResponseError(capability.value) from None


def _parse_analysis_response(response_text: str) -> PromptAnalysis:
    """Strictly normalize a raw model response into a PromptAnalysis."""
    return _parse_contract(
        response_text,
        IntelligenceCapability.ANALYZE,
        _ANALYSIS_KEYS,
        PromptAnalysis,
    )


def _parse_enhancement_response(response_text: str) -> PromptEnhancement:
    """Strictly normalize a raw model response into a PromptEnhancement."""
    return _parse_contract(
        response_text,
        IntelligenceCapability.ENHANCE,
        _ENHANCEMENT_KEYS,
        PromptEnhancement,
    )


def _parse_creation_response(response_text: str) -> PromptCreation:
    """Strictly normalize a raw model response into a PromptCreation."""
    return _parse_contract(
        response_text,
        IntelligenceCapability.CREATE,
        _CREATION_KEYS,
        PromptCreation,
    )


def _build_create_user_content(goal: str, context: str | None) -> str:
    """Compose the CREATE user message from the goal and optional context.

    The goal is always labeled; context is embedded only when present and non-blank, so
    a blank or absent context never leaks an empty CONTEXT block into the prompt.
    """
    if context and context.strip():
        return f"GOAL:\n{goal}\n\nCONTEXT:\n{context.strip()}"
    return f"GOAL:\n{goal}"


class PromptIntelligenceService:
    """Domain boundary for prompt analysis, enhancement, and creation.

    Methods receive validated input and return (or raise for) the normalized contracts
    in :mod:`app.intelligence.types`. Each method validates first, routes a structured
    request through the injected gateway, and strictly parses the model response. The
    service never imports or constructs an adapter, and never returns raw model text.
    """

    def __init__(self, gateway: "AIGateway"):
        self._gateway = gateway

    @property
    def gateway(self) -> "AIGateway":
        return self._gateway

    def analyze(
        self,
        prompt: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        db: "Session | None" = None,
    ) -> PromptAnalysis:
        """Validate the prompt, run a structured analysis, and normalize the result.

        The request carries no app metadata and therefore no ``prompt_id``, so the
        gateway performs no PromptRun persistence for analysis requests.
        """
        cleaned = validate_prompt(prompt)
        request = _build_structured_request(
            IntelligenceCapability.ANALYZE, cleaned, provider=provider, model=model
        )
        response, _run = self.gateway.generate(db, request)
        return _parse_analysis_response(response.text)

    def enhance(
        self,
        prompt: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        db: "Session | None" = None,
    ) -> PromptEnhancement:
        """Validate the prompt, run a structured enhancement, and normalize the result.

        The request carries no app metadata and therefore no ``prompt_id``, so the
        gateway performs no PromptRun persistence for enhancement requests.
        """
        cleaned = validate_prompt(prompt)
        request = _build_structured_request(
            IntelligenceCapability.ENHANCE, cleaned, provider=provider, model=model
        )
        response, _run = self.gateway.generate(db, request)
        return _parse_enhancement_response(response.text)

    def create(
        self,
        goal: str,
        *,
        context: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        db: "Session | None" = None,
    ) -> PromptCreation:
        """Validate the goal (and optional context), draft a prompt, and normalize it.

        The request carries no app metadata and therefore no ``prompt_id``, so the
        gateway performs no PromptRun persistence for creation requests.
        """
        cleaned_goal = validate_prompt(goal)
        cleaned_context = None
        if context is not None:
            cleaned_context = context.strip() or None
            if cleaned_context is not None and len(cleaned_context) > MAX_PROMPT_LENGTH:
                raise IntelligenceValidationError(
                    "Context is too long "
                    f"({len(cleaned_context)} characters; limit {MAX_PROMPT_LENGTH})."
                )
        user_content = _build_create_user_content(cleaned_goal, cleaned_context)
        request = _build_structured_request(
            IntelligenceCapability.CREATE,
            user_content,
            provider=provider,
            model=model,
        )
        response, _run = self.gateway.generate(db, request)
        return _parse_creation_response(response.text)