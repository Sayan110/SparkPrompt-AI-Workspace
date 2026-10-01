import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.ai.errors import AIGatewayError, AIProviderError, ERROR_HTTP_STATUS, ErrorCode, error_to_detail
from app.ai.gateway import AIGateway
from app.ai.registry import ProviderRegistry, build_registry
from app.ai.types import AIRequest, AIMessage
from app.core.database import get_db
from app.schemas.ai import (
    AiGenerateRequest,
    AiGenerateResponse,
    AiModelsOut,
    AiProviderInfoOut,
    AiStatus,
    AiUsageOut,
)

router = APIRouter()

_REGISTRY_CACHE: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is None:
        _REGISTRY_CACHE = build_registry()
    return _REGISTRY_CACHE


def get_gateway() -> AIGateway:
    return AIGateway(get_registry())


def _status_payload() -> AiStatus:
    registry = get_registry()
    providers: dict[str, str] = {}
    for info in registry.infos():
        providers[info.id] = "available" if info.available else "unavailable"
    any_configured = any(info.configured for info in registry.infos())
    return AiStatus(
        status="ready",
        phase="gateway",
        ai_runtime="configured" if any_configured else "not_configured",
        providers=providers,
        message=(
            "Provider gateway, model routing and prompt runs are live. "
            "Configure providers with server-side environment variables."
        ),
    )


@router.get("/status", response_model=AiStatus)
def ai_status() -> AiStatus:
    return _status_payload()


@router.get("/providers", response_model=list[AiProviderInfoOut])
def ai_providers() -> list[AiProviderInfoOut]:
    return [
        AiProviderInfoOut(
            id=info.id,
            name=info.name,
            available=info.available,
            configured=info.configured,
            streaming=info.capabilities.streaming,
            models=info.models,
            default_model=info.default_model,
        )
        for info in get_registry().infos()
    ]


@router.get("/models", response_model=list[AiModelsOut])
def ai_models() -> list[AiModelsOut]:
    return [
        AiModelsOut(provider=info.id, models=info.models) for info in get_registry().infos()
    ]


@router.post("/generate")
def ai_generate(
    payload: AiGenerateRequest,
    db: Session = Depends(get_db),
    gateway: AIGateway = Depends(get_gateway),
):
    request = _to_ai_request(payload)
    if payload.stream:
        return _stream_response(db, gateway, request)
    try:
        response, run = gateway.generate(db, request)
    except (AIGatewayError, AIProviderError) as exc:
        raise _http_from_error(exc)
    return _response_out(response, run, streamed=False)


def _to_ai_request(payload: AiGenerateRequest) -> AIRequest:
    return AIRequest(
        provider=payload.provider or "",
        model=payload.model or "",
        messages=[AIMessage(role=message.role, content=message.content) for message in payload.messages],
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        stream=payload.stream,
        metadata=payload.metadata,
    )


def _response_out(response, run, streamed: bool) -> AiGenerateResponse:
    prompt_id = None
    run_id = None
    if run is not None:
        prompt_id = getattr(run, "prompt_id", None)
        run_id = getattr(run, "id", None)
    return AiGenerateResponse(
        text=response.text,
        provider=response.provider,
        model=response.model,
        finish_reason=response.finish_reason,
        usage=AiUsageOut(
            prompt_tokens=response.usage.prompt_tokens if response.usage else None,
            completion_tokens=response.usage.completion_tokens if response.usage else None,
            total_tokens=response.usage.total_tokens if response.usage else None,
        ),
        request_id=response.request_id,
        latency_ms=response.latency_ms,
        streamed=streamed,
        created_at=response.generated_at,
        prompt_id=prompt_id,
        run_id=run_id,
    )


def _stream_response(
    db: Session, gateway: AIGateway, request: AIRequest
) -> StreamingResponse:
    def event_stream():
        try:
            for event in gateway.stream(db, request):
                if event.kind == "delta":
                    yield _sse(
                        {
                            "type": "delta",
                            "provider": event.provider,
                            "model": event.model,
                            "text": event.text,
                        }
                    )
                elif event.kind == "done":
                    yield _sse(
                        {
                            "type": "done",
                            "provider": event.provider,
                            "model": event.model,
                            "finish_reason": event.finish_reason,
                            "usage": _usage_out(event.usage),
                            "latency_ms": event.latency_ms,
                            "prompt_id": str(event.prompt_id) if event.prompt_id else None,
                            "run_id": str(event.run_id) if event.run_id else None,
                        }
                    )
        except (AIGatewayError, AIProviderError) as exc:
            detail = error_to_detail(exc)
            yield _sse(
                {
                    "type": "error",
                    "code": detail["code"],
                    "provider": detail.get("provider"),
                    "message": detail["message"],
                }
            )
        except Exception:
            # Safety net: the gateway already normalizes unexpected stream failures to
            # internal_error; this catches anything escaping so it never reaches the client raw.
            yield _sse(
                {
                    "type": "error",
                    "code": ErrorCode.INTERNAL_ERROR.value,
                    "provider": None,
                    "message": "Unexpected error while processing the AI stream.",
                }
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _usage_out(usage) -> AiUsageOut | None:
    if usage is None:
        return None
    return AiUsageOut(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _http_from_error(exc: Exception) -> HTTPException:
    detail = error_to_detail(exc)
    status_code = ERROR_HTTP_STATUS.get(exc.code, 502) if hasattr(exc, "code") else 502
    return HTTPException(status_code=status_code, detail=detail)