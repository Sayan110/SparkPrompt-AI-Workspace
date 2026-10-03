from __future__ import annotations

# --- S1 (Phase 5B): fail-closed database-target guard -----------------------
# Executed at conftest import time, which pytest guarantees happens before any
# test module in this directory is imported and strictly before any fixture,
# TestClient construction, or application engine use (app.core.database builds
# its engine lazily — create_engine performs no I/O — and the first possible
# connection is in a fixture or test body). No hook can run earlier inside this
# directory, so a bare `pytest` can never reach a protected database.
from db_target_guard import enforce_safe_database_target

enforce_safe_database_target()
# --- end S1 guard ------------------------------------------------------------

import uuid
from collections.abc import Iterator
from http.cookiejar import Cookie
from typing import Any

import fastapi.testclient
import pytest
from fastapi.testclient import TestClient as PlainTestClient

from app.ai.errors import AIProviderError, ErrorCode
from app.ai.gateway import AIGateway
from app.ai.providers.base import BaseProvider
from app.ai.registry import ProviderRegistry
from app.ai.types import (
    AIRequest,
    AIResponse,
    AIStreamEvent,
    AIMessage,
    AIUsage,
    ProviderCapabilities,
)
from app.core.config import Settings
from app.core.security import SESSION_COOKIE_NAME


class FakeProvider(BaseProvider):
    id = "fake"
    name = "Fake Provider"

    def is_configured(self) -> bool:
        return True

    def is_available(self) -> bool:
        return True

    def models(self) -> list[str]:
        return ["fake-model-1", "fake-model-2"]

    def default_model(self) -> str:
        return "fake-model-1"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True)

    def generate(self, request: AIRequest) -> AIResponse:
        text = "Simulated answer to: " + request.messages[-1].content
        return AIResponse(
            text=text,
            provider=self.id,
            model=request.model,
            finish_reason="stop",
            usage=AIUsage(prompt_tokens=10, completion_tokens=25, total_tokens=35),
            request_id=request.request_id,
        )

    def stream(self, request: AIRequest):
        for token in ("hello ", "world"):
            yield AIStreamEvent(kind="delta", text=token)
        yield AIStreamEvent(
            kind="done",
            finish_reason="stop",
            usage=AIUsage(prompt_tokens=5, completion_tokens=4, total_tokens=9),
        )


class NonStreamingProvider(FakeProvider):
    name = "Non-streaming Fake"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=False)


class UnconfiguredProvider(FakeProvider):
    name = "Unconfigured Fake"

    def is_configured(self) -> bool:
        return False


class UnavailableProvider(FakeProvider):
    name = "Unavailable Fake"

    def is_available(self) -> bool:
        return False


class FailingProvider(FakeProvider):
    def generate(self, request: AIRequest) -> AIResponse:
        raise AIProviderError(self.id, ErrorCode.PROVIDER_ERROR, "simulated upstream failure")

    def stream(self, request: AIRequest):
        raise AIProviderError(self.id, ErrorCode.PROVIDER_ERROR, "simulated upstream failure")


class ExplodingProvider(FakeProvider):
    name = "Exploding Fake"

    def generate(self, request: AIRequest) -> AIResponse:
        raise RuntimeError("unexpected-internal-detail from generate")

    def stream(self, request: AIRequest):
        yield AIStreamEvent(kind="delta", text="partial ")
        raise RuntimeError("unexpected-internal-detail from stream")


# ------------------------------------------------- Phase 4A auth test harness
#
# Existing tests never log in: they have always operated as the demo workspace.
# Now that every route requires a session, the harness signs the demo user in
# automatically at TestClient construction so current tests keep the identity
# they had. Auth/isolation tests use PlainTestClient for explicitly
# unauthenticated flows (401 sweeps, logout, token tampering).


def _demo_session_token() -> str | None:
    """A signed demo-user session token, or None when Postgres is unreachable."""
    try:
        from app.core.database import SessionLocal
        from app.core.security import create_session_token
        from app.services.demo_user import get_or_create_demo_user

        with SessionLocal() as session:
            demo = get_or_create_demo_user(session)
            session.commit()
            return create_session_token(demo.id)
    except Exception:
        return None


def _jar_domain_for(base_url: Any) -> str:
    """The cookie domain http.cookiejar derives for this client's host (RFC 2965).

    http.cookiejar appends ".local" to single-label hosts — hence the
    ``testserver.local`` domain on server-issued cookies. Seeding with the same
    domain makes the seed a first-class jar entry, so a server Set-Cookie
    REPLACES it on login and REMOVES it on logout exactly like a browser jar,
    instead of duplicating it (a no-domain seed would survive logout as a ghost
    session that keeps authenticating every later request).
    """
    host = (base_url.host or "").lower()
    return host if "." in host else f"{host}.local"


class DemoSessionTestClient(PlainTestClient):
    """TestClient pre-authenticated as the seeded demo user.

    The HttpOnly session cookie is minted fresh at construction (signing secret
    and TTL are process-stable). Server cookies supersede the seed on
    login/logout; per-test resets of rate-limit counters and revocations keep
    tests isolated from each other.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        token = _demo_session_token()
        if token is not None:
            self.cookies.jar.set_cookie(
                Cookie(
                    version=0,
                    name=SESSION_COOKIE_NAME,
                    value=token,
                    port=None,
                    port_specified=False,
                    domain=_jar_domain_for(self.base_url),
                    domain_specified=False,
                    domain_initial_dot=False,
                    path="/",
                    path_specified=True,
                    secure=False,
                    expires=None,
                    discard=True,
                    comment=None,
                    comment_url=None,
                    rest={"HttpOnly": None},
                    rfc2109=False,
                )
            )


# Test modules import TestClient from fastapi.testclient AFTER pytest has
# imported this conftest (all such imports are deferred inside test functions),
# so rebinding the module attribute — and the local name — points every
# existing construction site at the seeded client.
fastapi.testclient.TestClient = DemoSessionTestClient
TestClient = DemoSessionTestClient


@pytest.fixture(autouse=True)
def _reset_auth_state() -> Iterator[None]:
    """Keep auth side effects per-test: rate-limit counters and revoked tokens.

    Rate limits are keyed by client IP and revocations by token hash; both are
    process-local on purpose (documented in 4A), so they must be cleared before
    and after every test or one test's 429/logout would leak into the next.
    """
    from app.core.rate_limit import reset_all as reset_rate_limits
    from app.core.security import clear_revocations

    reset_rate_limits()
    clear_revocations()
    yield
    reset_rate_limits()
    clear_revocations()


@pytest.fixture
def demo_identity() -> uuid.UUID:
    """The demo user's id — stamps identity on a directly-called service Session.

    Only tests that invoke service functions outside an HTTP request need this:
    routes stamp identity through the auth dependency, but a bare SessionLocal
    has none and fails closed (401 by design).
    """
    from app.core.database import SessionLocal
    from app.services.demo_user import get_or_create_demo_user

    try:
        with SessionLocal() as session:
            demo = get_or_create_demo_user(session)
            session.commit()
            return demo.id
    except Exception as exc:  # unreachable Postgres: skip like the db_ready tests
        pytest.skip(f"Postgres not reachable: {exc}")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        ai_default_provider="fake",
        ai_timeout_seconds=1.0,
        gemini_api_key="",
        nvidia_api_key="",
        ollama_base_url="http://127.0.0.1:1",
    )


@pytest.fixture
def registry(settings: Settings) -> ProviderRegistry:
    r = ProviderRegistry(settings)
    r.register(FakeProvider(settings))
    return r


@pytest.fixture
def gateway(registry: ProviderRegistry) -> AIGateway:
    return AIGateway(registry)


@pytest.fixture
def app_client(monkeypatch, registry: ProviderRegistry):
    from app.api.routes import ai as ai_routes
    from app.main import app

    monkeypatch.setattr(ai_routes, "_REGISTRY_CACHE", registry)
    return TestClient(app)


def make_request(**overrides) -> AIRequest:
    messages = overrides.pop("messages", [AIMessage(role="user", content="Write a haiku")])
    return AIRequest(
        provider=overrides.pop("provider", "fake"),
        model=overrides.pop("model", "fake-model-1"),
        messages=messages,
        temperature=overrides.pop("temperature", 0.7),
        max_tokens=overrides.pop("max_tokens", 2048),
        stream=overrides.pop("stream", False),
        metadata=overrides.pop("metadata", None),
        request_id=overrides.pop("request_id", None) or __import__("uuid").uuid4(),
    )


__all__ = [
    "FakeProvider",
    "NonStreamingProvider",
    "UnconfiguredProvider",
    "UnavailableProvider",
    "FailingProvider",
    "ExplodingProvider",
    "make_request",
    # Phase 4A: unauthenticated client + the auto-signed demo client class.
    "PlainTestClient",
    "DemoSessionTestClient",
]