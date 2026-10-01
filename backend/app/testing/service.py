"""Prompt testing service (Phase 3D).

Phase 3D lets the user execute a prompt against the existing AI gateway and inspect the
raw execution. This service is deliberately thin: it validates the request, builds a
plain ``AIRequest``, routes it through the injected ``AIGateway`` (which owns provider
routing, error handling, and PromptRun persistence), and normalizes the gateway's
``AIResponse`` into ``PromptTestResult``.

Architecture guarantees (mirroring the intelligence layer):

* The service never imports or instantiates a provider adapter — it talks only to the
  ``AIGateway`` abstraction it receives by injection.
* It never accesses API keys and never performs model routing (the gateway's
  ``ModelRouter`` does that).
* It never writes to PostgreSQL directly. When the request carries a ``prompt_id`` it
  is encoded into the gateway request's ``metadata["prompt_id"]``, which is the
  existing mechanism the gateway uses to persist a ``PromptRun``. No test-specific
  tables exist and none are created.
* It performs no scoring, grading, or evaluation — Phase 3D is execute + observe.

Message convention (simple, documented, no templating system): the tested prompt is
sent verbatim as the single user turn. When a test ``input`` is provided it is appended
as a second user turn labeled ``INPUT:``, mirroring the labeled-block convention the
intelligence layer uses for create. A blank or absent input is simply omitted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.testing.errors import PromptTestingValidationError
from app.testing.types import PromptTestResult, PromptTestUsage

if TYPE_CHECKING:
    from uuid import UUID

    from app.ai.gateway import AIGateway
    from sqlalchemy.orm import Session

MAX_PROMPT_LENGTH = 20_000
"""Upper bound for the tested prompt, matching the intelligence input limit."""

MAX_INPUT_LENGTH = 20_000
"""Upper bound for the optional test input."""


def validate_prompt_test(prompt: str, test_input: str | None) -> tuple[str, str | None]:
    """Validate a test request and return the trimmed prompt and input.

    Validation runs before any gateway call so malformed input fails fast and
    predictably. A blank or whitespace-only input collapses to ``None`` (omitted from
    the request), matching the create capability's context behavior.
    """
    cleaned_prompt = (prompt or "").strip()
    if not cleaned_prompt:
        raise PromptTestingValidationError("Prompt must not be empty.")
    if len(cleaned_prompt) > MAX_PROMPT_LENGTH:
        raise PromptTestingValidationError(
            f"Prompt is too long ({len(cleaned_prompt)} characters; limit {MAX_PROMPT_LENGTH})."
        )

    cleaned_input: str | None = None
    if test_input is not None:
        cleaned_input = test_input.strip() or None
        if cleaned_input is not None and len(cleaned_input) > MAX_INPUT_LENGTH:
            raise PromptTestingValidationError(
                f"Test input is too long ({len(cleaned_input)} characters; limit {MAX_INPUT_LENGTH})."
            )
    return cleaned_prompt, cleaned_input


class PromptTestingService:
    """Runs a prompt through the injected AI gateway and returns a normalized result.

    The service holds only the gateway abstraction (constructor injection) and never
    talks to a provider, a router, the registry, or the database on its own.
    """

    def __init__(self, gateway: "AIGateway"):
        self._gateway = gateway

    @property
    def gateway(self) -> "AIGateway":
        return self._gateway

    def run(
        self,
        *,
        prompt: str,
        test_input: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        prompt_id: "UUID | None" = None,
        version_id: "UUID | None" = None,
        db: "Session | None" = None,
    ) -> PromptTestResult:
        """Validate the request, execute it through the gateway, and normalize the result.

        When ``prompt_id`` is provided it is encoded into the gateway request metadata,
        which is the existing mechanism that triggers PromptRun persistence inside the
        gateway. Without it, the execution is not persisted (same as intelligence calls).

        When ``version_id`` is also provided, the version is resolved server-side
        (owned prompt, version belonging to it — 404s otherwise) and its stored
        body is executed INSTEAD of the supplied ``prompt`` text. The supplied text
        is still validated but never executed and never attributed: a run stamped
        with a version always executed exactly that version's body. Without a
        version, behavior is byte-for-byte the pre-3O path (supplied body, NULL
        version identity — including drafts that merely resemble a saved version).
        """
        cleaned_prompt, cleaned_input = validate_prompt_test(prompt, test_input)

        version = None
        if version_id is not None:
            if prompt_id is None:
                raise PromptTestingValidationError(
                    "version_id requires prompt_id: a version is only meaningful within its prompt."
                )
            # Lazy import: resolving versions needs the service layer, which must
            # not be pulled in at module import time (provider-independence).
            from app.services.prompts import get_owned_prompt_version

            version = get_owned_prompt_version(db, prompt_id, version_id)
            cleaned_prompt = version.body

        # Imported lazily so importing app.testing never pulls in the AI stack at
        # import time (the provider-independence guarantee).
        from app.ai.types import AIMessage, AIRequest

        messages = [AIMessage(role="user", content=cleaned_prompt)]
        if cleaned_input is not None:
            messages.append(AIMessage(role="user", content=f"INPUT:\n{cleaned_input}"))

        metadata = None
        if prompt_id is not None:
            metadata = {"prompt_id": str(prompt_id)}

        request = AIRequest(
            provider=provider or "",
            model=model or "",
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            metadata=metadata,
        )

        # Version identity travels the explicit trusted kwarg — never metadata,
        # which client-controlled requests can reach via /api/ai/generate. The
        # kwarg is only sent for version-scoped executions so duck-typed
        # gateways (unit tests) keep working with the two-argument contract.
        if version is not None:
            response, run = self.gateway.generate(
                db, request, version_id=version.id
            )
        else:
            response, run = self.gateway.generate(db, request)
        return _to_result(response, run)


def _to_result(response, run) -> PromptTestResult:
    usage = None
    if response.usage is not None:
        usage = PromptTestUsage(
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
        )

    run_id = getattr(run, "id", None) if run is not None else None
    run_prompt_id = getattr(run, "prompt_id", None) if run is not None else None
    run_version_id = getattr(run, "version_id", None) if run is not None else None

    return PromptTestResult(
        output=response.text,
        provider=response.provider,
        model=response.model,
        finish_reason=response.finish_reason,
        latency_ms=response.latency_ms,
        usage=usage,
        request_id=response.request_id,
        prompt_id=run_prompt_id,
        run_id=run_id,
        version_id=run_version_id,
    )