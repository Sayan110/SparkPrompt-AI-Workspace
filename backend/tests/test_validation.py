import pytest
from pydantic import ValidationError

from app.schemas.ai import AiGenerateRequest


def test_valid_request_ok():
    request = AiGenerateRequest(
        provider="gemini",
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.5,
        max_tokens=1000,
    )
    assert request.messages[0].content == "hello"


def test_empty_messages_rejected():
    with pytest.raises(ValidationError):
        AiGenerateRequest(messages=[])


def test_unknown_role_rejected():
    with pytest.raises(ValidationError):
        AiGenerateRequest(messages=[{"role": "robot", "content": "hi"}])


def test_temperature_range_rejected():
    with pytest.raises(ValidationError):
        AiGenerateRequest(messages=[{"role": "user", "content": "hi"}], temperature=3.0)
    with pytest.raises(ValidationError):
        AiGenerateRequest(messages=[{"role": "user", "content": "hi"}], temperature=-0.1)


def test_max_tokens_bounds_rejected():
    with pytest.raises(ValidationError):
        AiGenerateRequest(messages=[{"role": "user", "content": "hi"}], max_tokens=0)
    with pytest.raises(ValidationError):
        AiGenerateRequest(messages=[{"role": "user", "content": "hi"}], max_tokens=300_000)


def test_empty_content_rejected():
    with pytest.raises(ValidationError):
        AiGenerateRequest(messages=[{"role": "user", "content": "   "}])


def test_metadata_limited_to_string_values():
    request = AiGenerateRequest(
        messages=[{"role": "user", "content": "hi"}],
        metadata={"prompt_id": "abc"},
    )
    assert request.metadata["prompt_id"] == "abc"