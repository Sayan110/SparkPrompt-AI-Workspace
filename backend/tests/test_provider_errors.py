import httpx
import pytest

from app.ai.errors import AIProviderError, ErrorCode
from conftest import FakeProvider


def _response(status: int, text: str) -> httpx.Response:
    return httpx.Response(
        status,
        text=text,
        request=httpx.Request("POST", "http://provider.local/generate"),
    )


def _assert_sanitized(provider, status: int, expected_code: ErrorCode, secret: str):
    with pytest.raises(AIProviderError) as exc:
        provider._raise_for_status(_response(status, f'{{"error": "{secret}"}}'))
    assert exc.value.code == expected_code
    assert secret not in exc.value.message
    assert "secret" not in exc.value.message.lower()


def test_upstream_error_body_not_exposed(settings):
    provider = FakeProvider(settings)
    _assert_sanitized(provider, 400, ErrorCode.INVALID_REQUEST, "sk-TOP-SECRET-KEY")
    _assert_sanitized(provider, 401, ErrorCode.AUTHENTICATION_ERROR, "sk-TOP-SECRET-KEY")
    _assert_sanitized(provider, 429, ErrorCode.RATE_LIMIT, "sk-TOP-SECRET-KEY")
    _assert_sanitized(provider, 500, ErrorCode.PROVIDER_ERROR, "sk-TOP-SECRET-KEY")


def test_sanitized_message_stable_and_contains_status(settings):
    provider = FakeProvider(settings)
    with pytest.raises(AIProviderError) as exc:
        provider._raise_for_status(_response(401, "raw upstream body"))
    assert "401" in exc.value.message
    assert exc.value.message == "Fake Provider authentication failed (status 401)."
    assert "raw upstream body" not in exc.value.message


def test_upstream_body_never_leaks_through_any_http_status(settings):
    provider = FakeProvider(settings)
    secret = "LEAK-check-payload-9f3a"
    for status in (400, 401, 403, 404, 408, 410, 429, 500, 503):
        with pytest.raises(AIProviderError) as exc:
            provider._raise_for_status(_response(status, secret))
        assert secret not in exc.value.message