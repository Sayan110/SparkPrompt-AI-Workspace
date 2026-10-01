from __future__ import annotations

import logging
from dataclasses import replace
from time import perf_counter
from typing import TYPE_CHECKING, Iterator

from sqlalchemy.orm import Session

from app.ai.errors import AIProviderError, AIGatewayError, ErrorCode
from app.ai.registry import ProviderRegistry
from app.ai.router import ModelRouter
from app.ai.types import (
    AIRequest,
    AIResponse,
    AIStreamEvent,
)
from app.services import prompt_runs

if TYPE_CHECKING:
    from app.ai.providers.base import BaseProvider

logger = logging.getLogger(__name__)


def _log_provider_failure(
    provider_id: str,
    model: str | None,
    exc: BaseException,
    *,
    ai_request_id: object | None = None,
    latency_ms: int | None = None,
) -> None:
    """Provider-failure visibility (Phase 4E) — logging only, no behavior change.

    Fields: provider, error code, model, AI request UUID, latency. Exception
    message text and tracebacks are deliberately excluded: provider/library
    exception text can embed raw upstream responses or request fragments, and
    the normalized error code already names the failure class for operators.
    """
    code = getattr(exc, "code", None)
    fields: dict[str, object] = {
        "provider": provider_id,
        "error_code": (
            code.value if isinstance(code, ErrorCode) else ErrorCode.INTERNAL_ERROR.value
        ),
    }
    if model:
        fields["model"] = str(model)
    if ai_request_id is not None:
        fields["ai_request_id"] = str(ai_request_id)
    if latency_ms is not None:
        fields["latency_ms"] = int(latency_ms)
    logger.error("ai provider failure: %s", type(exc).__name__, extra=fields)


class AIGateway:
    """Validates, routes, executes and persists normalized AI requests."""

    def __init__(self, registry: ProviderRegistry):
        self._registry = registry
        self._router = ModelRouter(registry)
        self._failure_record_exc: BaseException | None = None

    @property
    def registry(self) -> ProviderRegistry:
        return self._registry

    def generate(
        self, db: Session | None, request: AIRequest, version_id=None
    ) -> tuple[AIResponse, object | None]:
        """Execute one request; persist a run when the request names a prompt.

        ``version_id`` is an internal trusted channel (UUID or None), never read
        from request metadata: client-controlled metadata reaches the gateway via
        ``/api/ai/generate``, so a metadata-carried version could be smuggled.
        Only service-layer callers that already ownership-resolved the version
        pass it here.
        """
        provider, model = self._router.resolve(request.provider, request.model)
        self._require_available(provider)
        request = _with_resolved(request, model)
        started = perf_counter()
        try:
            response = provider.generate(request)
        except (AIProviderError, AIGatewayError) as exc:
            _log_provider_failure(
                provider.id,
                request.model,
                exc,
                ai_request_id=request.request_id,
                latency_ms=int((perf_counter() - started) * 1000),
            )
            self._record_failure(db, request, provider, version_id=version_id)
            raise
        response = _with_latency(response, started)
        run = self._record_success(db, request, response, version_id=version_id)
        return response, run

    def stream(
        self, db: Session | None, request: AIRequest, version_id=None
    ) -> Iterator[AIStreamEvent]:
        provider, model = self._router.resolve(request.provider, request.model)
        self._require_available(provider)
        if not provider.capabilities().streaming:
            raise AIProviderError(
                provider.id,
                ErrorCode.STREAMING_NOT_SUPPORTED,
                f"{provider.name} does not support streaming.",
            )
        request = _with_resolved(request, model)
        started = perf_counter()
        chunks: list[str] = []
        finish_reason: str | None = None
        usage = None
        try:
            for event in provider.stream(request):
                if event.kind == "delta":
                    chunks.append(event.text)
                    yield replace(event, provider=provider.id, model=model)
                elif event.kind == "done":
                    finish_reason = event.finish_reason or finish_reason
                    usage = event.usage or usage
            latency_ms = int((perf_counter() - started) * 1000)
            response = AIResponse(
                text="".join(chunks),
                provider=provider.id,
                model=model,
                finish_reason=finish_reason,
                usage=usage,
                request_id=request.request_id,
                latency_ms=latency_ms,
            )
            run = self._record_success(db, request, response, version_id=version_id)
            run_id = run.id if run is not None else None
            prompt_id = _prompt_id(request)
            yield AIStreamEvent(
                kind="done",
                provider=provider.id,
                model=model,
                finish_reason=finish_reason,
                usage=usage,
                latency_ms=latency_ms,
                prompt_id=prompt_id,
                run_id=run_id,
            )
        except (AIProviderError, AIGatewayError) as exc:
            _log_provider_failure(
                provider.id,
                model,
                exc,
                ai_request_id=request.request_id,
                latency_ms=int((perf_counter() - started) * 1000),
            )
            self._record_failure(db, request, provider, version_id=version_id)
            raise
        except Exception as exc:
            # Never leak unexpected stream internals to the client: normalize to a stable error.
            # Logging carries the exception class name only (same privacy rule as above).
            _log_provider_failure(
                provider.id,
                model,
                exc,
                ai_request_id=request.request_id,
                latency_ms=int((perf_counter() - started) * 1000),
            )
            self._record_failure(db, request, provider, version_id=version_id)
            raise AIGatewayError(
                ErrorCode.INTERNAL_ERROR,
                "The AI request failed unexpectedly. Please try again.",
                provider=provider.id,
            ) from None

    def _require_available(self, provider: BaseProvider) -> None:
        if not provider.is_available():
            error = AIProviderError(
                provider.id,
                ErrorCode.PROVIDER_UNAVAILABLE,
                f"{provider.name} is not available. Configure it on the server, or choose another provider.",
            )
            # Pre-execution rejection: no latency or AI request id exists yet.
            _log_provider_failure(provider.id, None, error)
            raise error

    def _record_success(
        self, db: Session | None, request: AIRequest, response: AIResponse, version_id=None
    ):
        prompt_id = _prompt_id(request)
        if db is None or prompt_id is None:
            return None
        return prompt_runs.record_success(
            db, prompt_id=prompt_id, request=request, response=response,
            version_id=version_id,
        )

    def _record_failure(
        self,
        db: Session | None,
        request: AIRequest,
        provider: BaseProvider,
        *,
        error: str = "Provider failure while generating.",
        version_id=None,
    ) -> None:
        prompt_id = _prompt_id(request)
        if db is None or prompt_id is None:
            return
        try:
            prompt_runs.record_failure(
                db,
                prompt_id=prompt_id,
                provider=provider.id,
                model=request.model or provider.default_model() or "",
                input_snapshot={
                    "messages": [
                        {"role": message.role, "content": message.content}
                        for message in request.messages
                    ],
                    "temperature": request.temperature,
                    "max_tokens": request.max_tokens,
                    "stream": request.stream,
                },
                error=error,
                version_id=version_id,
            )
        except Exception as exc:
            # Failure recording must never replace the original AI/provider error, and
            # database internals must never reach the API client. Roll back so the
            # session stays reusable and let the original exception propagate. The
            # swallowed persistence error is kept on the instance so it stays observable.
            db.rollback()
            self._failure_record_exc = exc
            # Phase 4E: the previously silent swallow is now logged. Exception
            # class name only — SQLAlchemy/DBAPI messages can embed statement
            # text and bound values (prompt content), which must never reach
            # the log stream.
            logger.error(
                "ai failure record persistence failed: %s",
                type(exc).__name__,
                extra={"provider": provider.id},
            )


def _with_resolved(request: AIRequest, model: str) -> AIRequest:
    return AIRequest(
        provider=request.provider,
        model=model,
        messages=request.messages,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        stream=request.stream,
        metadata=request.metadata,
        request_id=request.request_id,
    )


def _with_latency(response: AIResponse, started: float) -> AIResponse:
    latency_ms = int((perf_counter() - started) * 1000)
    return AIResponse(
        text=response.text,
        provider=response.provider,
        model=response.model,
        finish_reason=response.finish_reason,
        usage=response.usage,
        request_id=response.request_id,
        latency_ms=latency_ms,
        generated_at=response.generated_at,
    )


def _prompt_id(request: AIRequest) -> object | None:
    if not request.metadata:
        return None
    value = request.metadata.get("prompt_id")
    if not value:
        return None
    try:
        from uuid import UUID

        return UUID(value)
    except (ValueError, TypeError, AttributeError):
        return None