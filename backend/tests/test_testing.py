"""Phase 3D service tests for the prompt testing foundation.

``PromptTestingService`` is exercised against a duck-typed fake gateway: it must work
with any object exposing ``AIGateway.generate()`` and must never import or instantiate
a provider adapter (provider independence is asserted both statically and in a fresh
interpreter). No live provider keys, no Ollama, no network.
"""

import ast
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai.errors import AIGatewayError, AIProviderError, ErrorCode
from app.ai.types import AIResponse, AIUsage
from app.testing import (
    MAX_INPUT_LENGTH,
    MAX_PROMPT_LENGTH,
    PromptTestingService,
    PromptTestingValidationError,
    PromptTestResult,
    validate_prompt_test,
)

PROJECT = Path(__file__).resolve().parents[1]


class FakeGateway:
    """Duck-typed stand-in for AIGateway. No provider adapter involved."""

    def __init__(self, response=None, error=None, run=None):
        self.response = response if response is not None else AIResponse(
            text="Simulated output for the tested prompt.",
            provider="fake",
            model="fake-model-1",
            finish_reason="stop",
            usage=AIUsage(prompt_tokens=13, completion_tokens=27, total_tokens=40),
            request_id=uuid.uuid4(),
            latency_ms=91,
        )
        self.error = error
        self.run = run
        self.calls: list[tuple[object, object]] = []

    def generate(self, db, request):
        self.calls.append((db, request))
        if self.error is not None:
            raise self.error
        return self.response, self.run

    @property
    def registry(self):
        return None


@pytest.fixture
def gateway():
    return FakeGateway()


@pytest.fixture
def service(gateway):
    return PromptTestingService(gateway)


def last_request(gateway: FakeGateway):
    return gateway.calls[-1][1]


# --- success paths ---


def test_run_returns_normalized_result(service, gateway):
    result = service.run(prompt="Write a haiku")

    assert isinstance(result, PromptTestResult)
    assert result.output == "Simulated output for the tested prompt."
    assert result.provider == "fake"
    assert result.model == "fake-model-1"
    assert result.finish_reason == "stop"
    assert result.latency_ms == 91
    assert result.request_id == gateway.response.request_id
    # Not attached to a saved prompt -> no PromptRun metadata.
    assert result.prompt_id is None
    assert result.run_id is None


def test_run_forwards_provider_model_and_params(service, gateway):
    service.run(
        prompt="Write a haiku",
        provider="mine",
        model="my-model-1",
        temperature=0.2,
        max_tokens=512,
    )

    request = last_request(gateway)
    assert request.provider == "mine"
    assert request.model == "my-model-1"
    assert request.temperature == 0.2
    assert request.max_tokens == 512
    assert request.stream is False
    assert request.metadata is None


def test_run_uses_single_user_message_without_input(service, gateway):
    service.run(prompt="Write a haiku")

    request = last_request(gateway)
    assert len(request.messages) == 1
    assert request.messages[0].role == "user"
    assert request.messages[0].content == "Write a haiku"


def test_run_appends_labeled_test_input(service, gateway):
    service.run(prompt="Write a product description", test_input="Product: wireless headphones")

    request = last_request(gateway)
    assert len(request.messages) == 2
    assert request.messages[0].content == "Write a product description"
    assert request.messages[1].role == "user"
    assert request.messages[1].content == "INPUT:\nProduct: wireless headphones"


def test_run_trims_prompt_and_omits_blank_input(service, gateway):
    service.run(prompt="  Write a haiku  ", test_input="   ")

    request = last_request(gateway)
    assert len(request.messages) == 1
    assert request.messages[0].content == "Write a haiku"


def test_run_usage_mapped_when_present(service):
    result = service.run(prompt="Write a haiku")

    assert result.usage is not None
    assert result.usage.prompt_tokens == 13
    assert result.usage.completion_tokens == 27
    assert result.usage.total_tokens == 40


def test_run_usage_null_when_unavailable(service, gateway):
    no_usage = AIResponse(
        text="No usage reported.",
        provider="fake",
        model="fake-model-1",
        finish_reason="stop",
        usage=None,
        request_id=uuid.uuid4(),
    )
    gateway.response = no_usage

    result = service.run(prompt="Write a haiku")

    assert result.usage is None
    assert result.output == "No usage reported."


def test_run_defaults_provider_and_model_to_empty(service, gateway):
    """With no provider/model the gateway receives empty strings and resolves defaults itself."""
    service.run(prompt="Write a haiku")

    request = last_request(gateway)
    assert request.provider == ""
    assert request.model == ""


def test_run_forwards_db_session_to_gateway(service, gateway):
    """The session (which drives PromptRun persistence) must reach gateway.generate."""
    db = object()
    service.run(prompt="Write a haiku", db=db)

    assert gateway.calls[0][0] is db

    service.run(prompt="Write a haiku")
    assert gateway.calls[1][0] is None


# --- PromptRun integration (existing gateway mechanism, no new tables) ---


def test_run_no_metadata_without_prompt_id(service, gateway):
    service.run(prompt="Write a haiku")

    request = last_request(gateway)
    assert request.metadata is None


def test_run_prompt_id_encoded_in_metadata(service, gateway):
    prompt_id = uuid.uuid4()
    service.run(prompt="Write a haiku", prompt_id=prompt_id)

    request = last_request(gateway)
    assert request.metadata == {"prompt_id": str(prompt_id)}


def test_run_exposes_run_metadata_when_persisted(service, gateway):
    run_id = uuid.uuid4()
    prompt_id = uuid.uuid4()
    gateway.run = SimpleNamespace(id=run_id, prompt_id=prompt_id)

    result = service.run(prompt="Write a haiku", prompt_id=prompt_id)

    assert result.run_id == run_id
    assert result.prompt_id == prompt_id


# --- validation ---


def test_run_empty_prompt_rejected(service, gateway):
    with pytest.raises(PromptTestingValidationError):
        service.run(prompt="")

    assert gateway.calls == []


def test_run_blank_prompt_rejected(service, gateway):
    with pytest.raises(PromptTestingValidationError):
        service.run(prompt="   ")

    assert gateway.calls == []


def test_run_oversized_prompt_rejected(service, gateway):
    with pytest.raises(PromptTestingValidationError):
        service.run(prompt="a" * (MAX_PROMPT_LENGTH + 1))

    assert gateway.calls == []


def test_run_oversized_input_rejected(service, gateway):
    with pytest.raises(PromptTestingValidationError):
        service.run(prompt="Write a haiku", test_input="x" * (MAX_INPUT_LENGTH + 1))

    assert gateway.calls == []


def test_validate_prompt_test_trims_and_collapses_blank_input():
    prompt, test_input = validate_prompt_test("  draft  ", "   ")
    assert prompt == "draft"
    assert test_input is None


# --- gateway error propagation ---


@pytest.mark.parametrize(
    "error",
    [
        AIProviderError(
            "fake", ErrorCode.PROVIDER_UNAVAILABLE, "No provider is configured."
        ),
        AIGatewayError(ErrorCode.INTERNAL_ERROR, "Gateway exploded."),
    ],
)
def test_run_gateway_errors_propagate(error, service, gateway):
    gateway.error = error

    with pytest.raises(type(error)) as exc_info:
        service.run(prompt="Write a haiku")

    assert exc_info.value is error


# --- provider independence ---


def test_testing_modules_never_import_provider_adapters():
    """Static guarantee: app/testing must never import a provider adapter or the providers package."""
    testing_dir = PROJECT / "app" / "testing"
    forbidden = ("gemini", "nvidia", "ollama", "providers")
    for path in sorted(testing_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(
                        part in alias.name.lower() for part in forbidden
                    ), f"{path}: forbidden import {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not any(
                    part in node.module.lower() for part in forbidden
                ), f"{path}: forbidden import {node.module}"


def test_testing_import_does_not_touch_ai_stack_in_fresh_interpreter():
    """Importing the testing package must not import the AI stack at import time.

    The service depends on the AIGateway abstraction only at the type level and receives
    a gateway instance by injection; nothing in app.testing imports a provider, the
    router, the registry, or the gateway itself.
    """
    script = textwrap.dedent(
        """
        import sys
        import app.testing  # noqa: F401
        leaked = sorted(m for m in sys.modules if m.startswith("app.ai"))
        if leaked:
            print("LEAKED:" + ",".join(leaked))
            sys.exit(1)
        print("CLEAN")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=PROJECT,
    )
    assert result.returncode == 0, f"testing imported AI stack: {result.stdout}{result.stderr}"
    assert "CLEAN" in result.stdout


def test_testing_service_works_with_duck_typed_gateway():
    """The service runs against any object exposing generate() — no provider adapter involved."""
    class DuckGateway:
        def generate(self, db, request):
            return (
                SimpleNamespace(
                    text="duck output",
                    provider="duck",
                    model="duck-1",
                    finish_reason="stop",
                    usage=None,
                    latency_ms=5,
                    request_id=uuid.uuid4(),
                ),
                None,
            )

    result = PromptTestingService(DuckGateway()).run(prompt="quack")

    assert result.output == "duck output"
    assert result.provider == "duck"
    assert result.usage is None
    assert result.run_id is None