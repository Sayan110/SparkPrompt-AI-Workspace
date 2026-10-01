from __future__ import annotations

from enum import Enum
from typing import Optional


class ErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    UNKNOWN_PROVIDER = "unknown_provider"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    STREAMING_NOT_SUPPORTED = "streaming_not_supported"
    AUTHENTICATION_ERROR = "authentication_error"
    INVALID_MODEL = "invalid_model"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    INVALID_RESPONSE = "invalid_response"
    INTERNAL_ERROR = "internal_error"


class AIGatewayError(Exception):
    """Raised by the gateway or router for requests the gateway itself rejects."""

    def __init__(self, code: ErrorCode, message: str, provider: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.provider = provider


class AIProviderError(Exception):
    """A provider failure normalized to a SparkPrompt error. Never leaks raw provider internals."""

    def __init__(self, provider: str, code: ErrorCode, message: str):
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.message = message


# HTTP status used for each normalized code so the API surface stays predictable.
ERROR_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.UNKNOWN_PROVIDER: 404,
    ErrorCode.PROVIDER_UNAVAILABLE: 409,
    ErrorCode.STREAMING_NOT_SUPPORTED: 400,
    ErrorCode.AUTHENTICATION_ERROR: 502,
    ErrorCode.INVALID_MODEL: 502,
    ErrorCode.RATE_LIMIT: 502,
    ErrorCode.TIMEOUT: 502,
    ErrorCode.PROVIDER_ERROR: 502,
    ErrorCode.INVALID_RESPONSE: 502,
    ErrorCode.INTERNAL_ERROR: 500,
}


def error_to_detail(error: Exception) -> dict:
    if isinstance(error, AIProviderError):
        return {
            "status": "error",
            "code": error.code.value,
            "provider": error.provider,
            "message": error.message,
        }
    if isinstance(error, AIGatewayError):
        return {
            "status": "error",
            "code": error.code.value,
            "provider": error.provider,
            "message": error.message,
        }
    return {
        "status": "error",
        "code": ErrorCode.PROVIDER_ERROR.value,
        "message": "Unexpected error while processing the AI request.",
    }