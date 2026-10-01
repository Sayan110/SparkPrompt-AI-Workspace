"""Phase 4E observability tests.

Correlation (X-Request-ID), the single application access record, level
semantics, health-probe suppression, readiness transitions, auth event
reason codes, rate-limit visibility, AI provider failure fields, swallowed
persistence failures, lifespan logs, the privacy contract, and static checks
over the nginx/compose/lifecycle files.

Conventions match the other integration modules: a module-scoped
``db_ready`` reachability check with per-test ``_require_db`` (non-DB tests
never skip), ``PlainTestClient`` for explicit session management, and the
autouse ``_reset_auth_state`` limiter/revocation reset from conftest. The
``observed`` fixture configures logging exactly like the lifespan does and
restores the root logger afterwards, so no logging state leaks to other
modules.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.ai.errors import AIGatewayError, AIProviderError, ErrorCode
from app.ai.gateway import AIGateway
from app.ai.registry import ProviderRegistry
from app.core.config import Settings
from app.core.database import SessionLocal, engine
from app.core.logging import (
    JsonFormatter,
    PlainFormatter,
    configure_logging,
    request_id_ctx,
    resolve_level,
)
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    revoke_session_token,
)
from app.main import app
from app.models import User

from conftest import (
    ExplodingProvider,
    FailingProvider,
    PlainTestClient,
    UnavailableProvider,
    make_request,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "4e-Obs-Test-Passw0rd"
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


# ---------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def db_ready() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False


def _require_db(db_ready: bool) -> None:
    if not db_ready:
        pytest.skip("Postgres not reachable")


def _app_loggers() -> dict[str, logging.Logger]:
    """Every real logger registered under the ``app`` namespace right now."""
    registry = logging.getLogger().manager.loggerDict
    return {
        name: logger
        for name, logger in registry.items()
        if isinstance(logger, logging.Logger)
        and (name == "app" or name.startswith("app."))
    }


@pytest.fixture
def observed() -> Iterator[None]:
    """Configure logging like the lifespan does; restore logger state after.

    Snapshot/restore contract (one observability test must never contaminate
    another, and no state may leak to other modules):

    * root logger level, handlers and ``disabled`` flag,
    * every ``app*`` logger's level, ``propagate`` flag and ``disabled``,
    * the global LogRecordFactory (``configure_logging`` installs its own),
    * the ``request_id_ctx`` context variable.

    The re-enable step exists because ``tests/test_migrations.py`` runs
    alembic's ``command.upgrade`` in-process, whose ``env.py`` calls
    ``logging.config.fileConfig("alembic.ini")`` — Python's default
    ``disable_existing_loggers=True`` then marks every pre-existing logger
    (``app.access`` included) as disabled, and Python 3.14's
    ``isEnabledFor()`` returns False for a disabled logger, so records are
    dropped before creation (they reach neither stderr nor caplog). In
    production alembic runs in a *separate process* before uvicorn starts,
    so production loggers are never disabled: the fixture puts loggers into
    that production state for the test and restores the exact prior state
    on teardown.
    """
    root = logging.getLogger()
    saved_level = root.level
    saved_root_disabled = root.disabled
    saved_handlers = list(root.handlers)
    saved_factory = logging.getLogRecordFactory()
    saved_request_id = request_id_ctx.get()
    saved_loggers = {
        name: (logger.level, logger.propagate, logger.disabled)
        for name, logger in _app_loggers().items()
    }
    configure_logging("INFO")
    root.disabled = False
    for logger in _app_loggers().values():
        logger.disabled = False
    try:
        yield
    finally:
        for name, logger in _app_loggers().items():
            if name in saved_loggers:
                saved_level_i, saved_propagate, saved_disabled = saved_loggers[name]
                logger.setLevel(saved_level_i)
                logger.propagate = saved_propagate
                logger.disabled = saved_disabled
        for handler in list(root.handlers):
            if handler not in saved_handlers:
                root.removeHandler(handler)
        for handler in saved_handlers:
            if handler not in root.handlers:
                root.addHandler(handler)
        root.setLevel(saved_level)
        root.disabled = saved_root_disabled
        if logging.getLogRecordFactory() is not saved_factory:
            logging.setLogRecordFactory(saved_factory)
        request_id_ctx.set(saved_request_id)


def _unique_email(prefix: str = "p4e-obs") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def _access_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "request"]


def _client() -> PlainTestClient:
    return PlainTestClient(app)


# ------------------------------------------------------- P1: central logging


def test_log_level_default_parsing_and_rejection() -> None:
    """Default INFO, case-insensitive names, invalid values fail loudly."""
    assert Settings().log_level == "INFO"
    assert Settings(log_level="debug").log_level == "DEBUG"
    assert resolve_level("DeBuG") == logging.DEBUG
    with pytest.raises(ValidationError) as invalid:
        Settings(log_level="verbose")
    assert "LOG_LEVEL" in str(invalid.value)
    assert "DEBUG" in str(invalid.value)  # the allowed values are named
    with pytest.raises(ValueError, match="LOG_LEVEL must be one of"):
        resolve_level("chatty")
    # configure_logging resolves FIRST — an invalid level changes nothing.
    with pytest.raises(ValueError, match="LOG_LEVEL must be one of"):
        configure_logging("loud")


def test_production_json_format_is_one_valid_line_with_whitelist_fields(
    observed, capsys
) -> None:
    """Prod format: one JSON object per line; non-whitelisted extras dropped."""
    configure_logging("INFO", json_format=True)
    handler = next(
        h
        for h in logging.getLogger().handlers
        if h.get_name() == "sparkprompt.stderr"
    )
    assert isinstance(handler.formatter, JsonFormatter)

    logging.getLogger("test.observability.json").info(
        "multi\nline message",
        extra={"route": "/api/x", "status": 200, "password": "hunter2-secret"},
    )
    written = capsys.readouterr().err
    lines = [line for line in written.splitlines() if "multi" in line]
    assert len(lines) == 1, "exactly one physical line per record"
    assert "\n" not in lines[0]
    payload = json.loads(lines[0])
    assert payload["message"] == "multi\nline message"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.observability.json"
    assert payload["route"] == "/api/x"
    assert payload["status"] == 200
    # Whitelist: extras outside SAFE_LOG_FIELDS never reach the stream.
    assert "password" not in payload
    assert "hunter2-secret" not in lines[0]


# --------------------------------------------- P2: correlation + access log


def test_request_id_valid_inbound_echoed_and_attached(observed, caplog) -> None:
    """A safe inbound X-Request-ID is echoed and stamped on the record."""
    client = _client()
    caplog.clear()
    response = client.get(
        "/api/auth/me", headers={"X-Request-ID": "nginx.4e-abc_123"}
    )
    assert response.status_code == 401
    assert response.headers.get("X-Request-ID") == "nginx.4e-abc_123"
    access = _access_records(caplog)
    assert len(access) == 1
    assert access[0].request_id == "nginx.4e-abc_123"
    assert access[0].levelno == logging.WARNING  # 4xx


def test_request_id_malformed_or_missing_replaced_with_uuid(observed, caplog) -> None:
    """Unsafe/absent ids are replaced with a fresh UUID4, still correlated."""
    client = _client()
    generated: list[str] = []
    for bad in ("bad id here", "x" * 65):
        caplog.clear()
        response = client.get("/api/auth/me", headers={"X-Request-ID": bad})
        request_id = response.headers.get("X-Request-ID")
        assert request_id is not None and request_id != bad
        assert UUID_PATTERN.fullmatch(request_id)
        access = _access_records(caplog)
        assert access[0].request_id == request_id
        generated.append(request_id)
    assert generated[0] != generated[1]

    caplog.clear()
    response = client.get("/api/auth/me")
    fresh = response.headers.get("X-Request-ID")
    assert fresh is not None and UUID_PATTERN.fullmatch(fresh)
    assert fresh not in generated


def test_access_record_is_single_structured_and_identity_is_uuid_only(
    observed, caplog, app_client, db_ready
) -> None:
    """ONE record per request: route template, levels, duration, user UUID."""
    _require_db(db_ready)
    me = app_client.get("/api/auth/me")
    assert me.status_code == 200
    user_id = me.json()["id"]

    caplog.clear()
    missing = app_client.get(f"/api/prompts/{uuid.uuid4()}")
    assert missing.status_code == 404
    access = _access_records(caplog)
    assert len(access) == 1, "exactly one access record per request"
    record = access[0]
    assert record.levelno == logging.WARNING  # 4xx
    assert record.method == "GET"
    assert record.route == "/api/prompts/{prompt_id}"  # template, not the UUID
    assert record.status == 404
    assert isinstance(record.duration_ms, float) and record.duration_ms >= 0
    assert isinstance(record.client_ip, str) and record.client_ip
    assert record.request_id

    caplog.clear()
    listing = app_client.get("/api/prompts")
    assert listing.status_code == 200
    access = _access_records(caplog)
    assert len(access) == 1
    record = access[0]
    assert record.levelno == logging.INFO  # 2xx
    assert record.route == "/api/prompts"
    assert record.status == 200
    assert record.user_id == user_id  # internal UUID only


def test_health_access_records_suppressed_until_debug(observed, caplog) -> None:
    """Health probes never log at INFO; at DEBUG they appear, once, at DEBUG."""
    client = _client()
    caplog.clear()
    assert client.get("/api/health").status_code == 200
    assert _access_records(caplog) == []

    caplog.set_level(logging.DEBUG)
    caplog.clear()
    assert client.get("/api/health").status_code == 200
    health_access = _access_records(caplog)
    assert len(health_access) == 1
    assert health_access[0].levelno == logging.DEBUG
    assert health_access[0].route.startswith("/api/health")

    caplog.clear()
    ready = client.get("/api/health/ready")
    assert ready.status_code in (200, 503)  # contract untouched either way
    ready_access = _access_records(caplog)
    assert len(ready_access) == 1
    assert ready_access[0].levelno == logging.DEBUG


def test_unexpected_exception_logged_with_context_then_generic_500(
    observed, caplog, monkeypatch
) -> None:
    """Middleware logs class+traceback+request_id, re-raises; client sees 500."""
    from app.api import deps as deps_module

    def boom(_token: str):
        raise RuntimeError("unexpected-internal-detail from deps")

    monkeypatch.setattr(deps_module, "verify_session_token", boom)
    client = PlainTestClient(app, raise_server_exceptions=False)
    client.cookies.set(SESSION_COOKIE_NAME, "any-token")
    caplog.clear()
    response = client.get("/api/auth/me", headers={"X-Request-ID": "boom-4e-1"})

    assert response.status_code == 500
    assert b"Internal Server Error" in response.content
    assert b"unexpected-internal-detail" not in response.content
    assert b"RuntimeError" not in response.content
    # Documented limitation (DEPLOYMENT.md §14.5): the exception response is
    # built by Starlette's outermost ServerErrorMiddleware, after our
    # middleware re-raised — correlation for these goes through the log record.
    assert "X-Request-ID" not in response.headers

    failed = [r for r in caplog.records if r.getMessage().startswith("request failed:")]
    assert len(failed) == 1
    record = failed[0]
    assert record.levelno == logging.ERROR
    assert record.getMessage() == "request failed: RuntimeError"
    assert record.exc_info is not None
    assert record.request_id == "boom-4e-1"
    assert record.route == "/api/auth/me"

    access = _access_records(caplog)
    assert len(access) == 1
    assert access[0].status == 500
    assert access[0].levelno == logging.ERROR  # 5xx
    assert access[0].request_id == "boom-4e-1"


# ------------------------------------------------ P4A: readiness transitions


def test_readiness_transition_logged_once_and_recovery_logged_once(
    observed, caplog, db_ready, monkeypatch
) -> None:
    """ERROR once on OK -> unavailable, INFO once on recovery, no spam."""
    _require_db(db_ready)
    import app.api.routes.health as health_mod
    from app.core.database import engine as real_engine

    class _BrokenEngine:
        def connect(self):
            raise RuntimeError("database unreachable (simulated)")

    monkeypatch.setattr(health_mod, "_database_ok", True)  # known baseline
    monkeypatch.setattr(health_mod, "engine", _BrokenEngine())
    client = _client()

    first = client.get("/api/health/ready")
    second = client.get("/api/health/ready")
    assert first.status_code == 503
    assert second.status_code == 503
    # Response contract unchanged: only check names/statuses/revision.
    assert first.json()["status"] == "unavailable"
    assert first.json()["checks"]["database"] == "unavailable"

    transition_records = [
        r for r in caplog.records if r.getMessage().startswith("readiness:")
    ]
    assert len(transition_records) == 1, "one ERROR for repeated failures"
    assert transition_records[0].levelno == logging.ERROR
    assert transition_records[0].getMessage() == (
        "readiness: database check transitioned (ok -> unavailable)"
    )

    monkeypatch.setattr(health_mod, "engine", real_engine)
    recovered = client.get("/api/health/ready")
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "ok"

    caplog.clear()
    assert client.get("/api/health/ready").status_code == 200
    recovered_records = [
        r for r in caplog.records if r.getMessage().startswith("readiness:")
    ]
    assert recovered_records == [], "unchanged state logs nothing"


# ---------------------------------------------- P4B: auth + rate-limit events


def test_session_failure_reason_codes_and_uniform_bodies(
    observed, caplog, db_ready
) -> None:
    """All six-path session failures: reason codes for operators, 401 for clients."""
    _require_db(db_ready)
    client = _client()
    bodies: list[str] = []
    caplog.clear()

    bare = client.get("/api/auth/me")
    bodies.append(bare.text)

    client.cookies.set(SESSION_COOKIE_NAME, "garbage-token-not-signed")
    tampered = client.get("/api/auth/me")
    bodies.append(tampered.text)

    revoked = create_session_token(uuid.uuid4())
    revoke_session_token(revoked)
    client.cookies.set(SESSION_COOKIE_NAME, revoked)
    after_revoke = client.get("/api/auth/me")
    bodies.append(after_revoke.text)

    orphan = create_session_token(uuid.uuid4())  # valid signature, no such user
    client.cookies.set(SESSION_COOKIE_NAME, orphan)
    missing_user = client.get("/api/auth/me")
    bodies.append(missing_user.text)

    assert [bare.status_code, tampered.status_code, after_revoke.status_code,
            missing_user.status_code] == [401, 401, 401, 401]
    assert len(set(bodies)) == 1, "the 401 body stays uniform for every reason"

    rejected = [r for r in caplog.records if r.getMessage() == "auth rejected"]
    assert [r.reason for r in rejected] == [
        "missing_cookie",
        "invalid_token",
        "revoked",
        "user_not_found",
    ]
    assert rejected[0].levelno == logging.INFO  # normal anonymous case
    assert all(r.levelno == logging.WARNING for r in rejected[1:])
    assert all(isinstance(r.client_ip, str) and r.client_ip for r in rejected)

    formatted = "\n".join(PlainFormatter().format(r) for r in rejected)
    assert "garbage-token-not-signed" not in formatted
    assert revoked not in formatted
    assert orphan not in formatted


def test_auth_event_logs_carry_reason_codes_and_never_identity(
    observed, caplog, db_ready
) -> None:
    """Login/signup/logout events: UUID + reason only, never email/password."""
    _require_db(db_ready)
    client = _client()
    email = _unique_email("p4e-auth")
    caplog.clear()

    assert client.post(
        "/api/auth/signup", json={"email": email, "password": PASSWORD}
    ).status_code == 201
    assert client.post(
        "/api/auth/signup", json={"email": email, "password": PASSWORD}
    ).status_code == 409
    assert client.post(
        "/api/auth/login", json={"email": email, "password": "wrong-pass-4e"}
    ).status_code == 401
    assert client.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}
    ).status_code == 200
    assert client.post("/api/auth/logout").status_code == 200

    def count(message: str) -> list[logging.LogRecord]:
        return [r for r in caplog.records if r.getMessage() == message]

    signup_ok = count("auth signup ok")
    signup_rejected = count("auth signup rejected")
    login_rejected = count("auth login rejected")
    login_ok = count("auth login ok")
    logout = count("auth logout")
    assert len(signup_ok) == 1 and signup_ok[0].levelno == logging.INFO
    assert len(signup_rejected) == 1 and signup_rejected[0].reason == "email_taken"
    assert signup_rejected[0].levelno == logging.WARNING
    assert len(login_rejected) == 1
    assert login_rejected[0].reason == "invalid_credentials"
    assert login_rejected[0].levelno == logging.WARNING
    assert len(login_ok) == 1 and login_ok[0].levelno == logging.INFO
    assert isinstance(getattr(login_ok[0], "user_id", None), str)
    assert len(logout) == 1 and logout[0].levelno == logging.INFO

    formatted = "\n".join(
        PlainFormatter().format(r)
        for r in signup_ok + signup_rejected + login_rejected + login_ok + logout
    )
    assert email not in formatted
    assert PASSWORD not in formatted


def test_rate_limit_block_logs_limiter_ip_retry_after_without_identity(
    observed, caplog, db_ready
) -> None:
    """429 attempts visible (limiter/client_ip/retry_after); no identity keys."""
    _require_db(db_ready)
    client = _client()
    email = _unique_email("p4e-rl")
    wrong = {"email": email, "password": "definitely-wrong-4e"}
    caplog.clear()

    statuses = [client.post("/api/auth/login", json=wrong).status_code for _ in range(10)]
    blocked = client.post("/api/auth/login", json=wrong)
    assert statuses == [401] * 10
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After")

    limit_records = [r for r in caplog.records if r.getMessage() == "rate limit exceeded"]
    assert len(limit_records) == 1
    record = limit_records[0]
    assert record.levelno == logging.WARNING
    assert record.limiter == "login"
    assert isinstance(record.retry_after, int) and record.retry_after >= 1
    assert isinstance(record.client_ip, str) and record.client_ip

    rejections = [r for r in caplog.records if r.getMessage() == "auth login rejected"]
    assert len(rejections) == 10
    assert all(r.reason == "invalid_credentials" for r in rejections)

    formatted = PlainFormatter().format(record)
    assert email not in formatted and "definitely-wrong-4e" not in formatted


# ------------------------------------------------- P4C: AI gateway failures


def test_provider_failure_logs_structured_fields(
    observed, caplog, settings
) -> None:
    """Provider, error code, model, AI request id, latency — never the raw message."""
    registry = ProviderRegistry(settings)
    registry.register(FailingProvider(settings))
    gateway = AIGateway(registry)
    request = make_request()
    caplog.clear()

    with pytest.raises(AIProviderError):
        gateway.generate(None, request)

    failures = [r for r in caplog.records if r.getMessage().startswith("ai provider failure")]
    assert len(failures) == 1
    record = failures[0]
    assert record.levelno == logging.ERROR
    assert record.getMessage() == "ai provider failure: AIProviderError"
    assert record.provider == "fake"
    assert record.error_code == ErrorCode.PROVIDER_ERROR.value
    assert record.model == "fake-model-1"
    assert record.ai_request_id == str(request.request_id)
    assert isinstance(record.latency_ms, int) and record.latency_ms >= 0
    assert "simulated upstream failure" not in PlainFormatter().format(record)

    # Pre-execution rejection: partial fields by design (nothing ran yet).
    unavailable_registry = ProviderRegistry(settings)
    unavailable_registry.register(UnavailableProvider(settings))
    unavailable_gateway = AIGateway(unavailable_registry)
    caplog.clear()
    with pytest.raises(AIProviderError):
        unavailable_gateway.generate(None, make_request())
    pre_failures = [
        r for r in caplog.records if r.getMessage().startswith("ai provider failure")
    ]
    assert len(pre_failures) == 1
    assert pre_failures[0].error_code == ErrorCode.PROVIDER_UNAVAILABLE.value
    assert getattr(pre_failures[0], "latency_ms", None) is None
    assert getattr(pre_failures[0], "ai_request_id", None) is None


def test_unexpected_stream_failure_normalized_and_logged_without_internals(
    observed, caplog, settings
) -> None:
    """Unexpected class name is logged (no message), stream still normalizes."""
    registry = ProviderRegistry(settings)
    registry.register(ExplodingProvider(settings))
    gateway = AIGateway(registry)
    request = make_request()
    caplog.clear()

    with pytest.raises(AIGatewayError) as raised:
        list(gateway.stream(None, request))
    assert raised.value.code == ErrorCode.INTERNAL_ERROR

    failures = [r for r in caplog.records if r.getMessage().startswith("ai provider failure")]
    assert len(failures) == 1
    record = failures[0]
    assert record.getMessage() == "ai provider failure: RuntimeError"
    assert record.error_code == ErrorCode.INTERNAL_ERROR.value
    assert record.provider == "fake"
    assert "unexpected-internal-detail" not in PlainFormatter().format(record)


def test_swallowed_persistence_failure_now_logged(
    observed, caplog, settings, db_ready, monkeypatch
) -> None:
    """The swallowed _failure_record_exc path logs class-name-only ERROR."""
    _require_db(db_ready)
    from app.services import prompt_runs as prompt_runs_module

    def boom(*_args, **_kwargs):
        raise RuntimeError("db exploded with prompt content")

    monkeypatch.setattr(prompt_runs_module, "record_failure", boom)
    registry = ProviderRegistry(settings)
    registry.register(FailingProvider(settings))
    gateway = AIGateway(registry)
    db = SessionLocal()
    request = make_request(metadata={"prompt_id": str(uuid.uuid4())})
    caplog.clear()
    try:
        with pytest.raises(AIProviderError):
            gateway.generate(db, request)
    finally:
        db.close()

    persisted = [
        r for r in caplog.records
        if r.getMessage().startswith("ai failure record persistence failed")
    ]
    assert len(persisted) == 1
    record = persisted[0]
    assert record.levelno == logging.ERROR
    assert record.getMessage() == "ai failure record persistence failed: RuntimeError"
    assert record.provider == "fake"
    assert "db exploded" not in PlainFormatter().format(record)
    assert isinstance(gateway._failure_record_exc, RuntimeError)


# --------------------------------------------------------- P4D: privacy + SQL


def test_engine_hides_sql_parameters() -> None:
    """Bound values are redacted from SQLAlchemy error messages everywhere."""
    assert engine.hide_parameters is True


def test_privacy_secrets_never_reach_the_log_stream(
    observed, caplog, app_client, db_ready
) -> None:
    """Passwords, hashes, emails, prompt text, AI output, tokens, creds."""
    _require_db(db_ready)
    email = _unique_email("p4e-priv")
    prompt_marker = "PRIVACY_PROMPT_MARKER_4e"
    auth_value = "p4e-fake-authorization-value"

    caplog.set_level(logging.DEBUG)  # the strictest possible stream state
    caplog.clear()

    created = app_client.post(
        "/api/prompts", json={"title": "P4E privacy probe", "idea": prompt_marker}
    )
    assert created.status_code == 201
    assert app_client.get("/api/prompts").status_code == 200
    generated = app_client.post(
        "/api/ai/generate",
        json={
            "provider": "fake",
            "messages": [{"role": "user", "content": prompt_marker}],
        },
    )
    assert generated.status_code == 200
    assert prompt_marker in generated.text  # AI output really would leak...

    anon = _client()
    assert anon.post(
        "/api/auth/signup", json={"email": email, "password": PASSWORD}
    ).status_code == 201
    assert anon.post(
        "/api/auth/login", json={"email": email, "password": "wrong-pass-4e"}
    ).status_code == 401
    assert anon.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}
    ).status_code == 200
    session_token = anon.cookies.get(SESSION_COOKIE_NAME)
    assert session_token
    assert anon.get(
        "/api/health", headers={"Authorization": f"Bearer {auth_value}"}
    ).status_code == 200
    assert anon.get("/api/auth/me").status_code == 200

    blob = "\n".join(PlainFormatter().format(r) for r in caplog.records)
    blob += "\n" + "\n".join(JsonFormatter().format(r) for r in caplog.records)

    with SessionLocal() as session:
        user = session.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()
    assert user is not None and user.password_hash

    db_password = engine.url.password
    banned = [
        PASSWORD,
        email,
        prompt_marker,
        "Simulated answer",  # provider output
        auth_value,
        session_token,
        user.password_hash,
    ]
    if db_password:
        banned.append(db_password)
    for value in banned:
        assert value not in blob, f"privacy violation: {value[:6]!r} reached logs"

    # Positive control: identity IS available — as the internal UUID only.
    assert any(getattr(r, "user_id", None) for r in caplog.records)
    # Even at DEBUG, SQLAlchemy never echoes statements/rows into the stream.
    assert not any(r.name.startswith("sqlalchemy") for r in caplog.records)


# ------------------------------------------------------- P3: lifespan logs


def test_lifespan_logs_startup_shutdown_and_failures(observed, caplog, db_ready, monkeypatch) -> None:
    """Startup summary + shutdown INFO; startup failure ERROR + re-raise."""
    _require_db(db_ready)
    caplog.clear()
    with PlainTestClient(app) as client:
        assert client.get("/api/health").status_code == 200

    startup = [r for r in caplog.records if r.getMessage().startswith("startup complete:")]
    assert len(startup) == 1
    assert startup[0].levelno == logging.INFO
    assert "configuration validated" in startup[0].getMessage()
    shutdown = [r for r in caplog.records if r.getMessage() == "shutdown complete"]
    assert len(shutdown) == 1 and shutdown[0].levelno == logging.INFO

    class _BrokenSettings:
        log_level = "INFO"
        is_production = False

        @staticmethod
        def validate_for_startup() -> None:
            raise ValueError("simulated configuration failure")

    monkeypatch.setattr("app.main.settings", _BrokenSettings())
    caplog.clear()
    with pytest.raises(Exception):  # fail-fast preserved (wrapped or not)
        with PlainTestClient(app):
            pass
    failed = [r for r in caplog.records if r.getMessage() == "startup failed"]
    assert len(failed) == 1
    assert failed[0].levelno == logging.ERROR
    assert failed[0].exc_info is not None


# ------------------------------------------- P5-P7: static infrastructure


def test_nginx_configs_carry_correlation_and_timing_fields() -> None:
    """Both `main` formats in sync; request_id header on both proxy locations."""
    nginx = (REPO_ROOT / "infra/nginx/nginx.conf").read_text(encoding="utf-8")
    tls = (REPO_ROOT / "infra/nginx/nginx-tls.conf.example").read_text(
        encoding="utf-8"
    )
    locations = (REPO_ROOT / "infra/nginx/locations.conf").read_text(encoding="utf-8")

    main_active = re.search(r"log_format main[\s\S]*?;", nginx)
    main_tls = re.search(r"log_format main[\s\S]*?;", tls)
    assert main_active and main_tls
    assert main_active.group(0) == main_tls.group(0), "both `main` formats in sync"
    for variable in ("$request_id", "$request_time", "$upstream_response_time",
                     "$upstream_status"):
        assert variable in main_active.group(0), f"missing {variable}"

    assert locations.count("proxy_set_header X-Request-ID $request_id;") == 2, (
        "both proxy locations must pass nginx's $request_id to the API"
    )


def test_compose_files_set_bounded_json_file_rotation() -> None:
    """Every service in both compose files: json-file 10m x 3."""
    dev = (REPO_ROOT / "infra/docker-compose.yml").read_text(encoding="utf-8")
    prod = (REPO_ROOT / "infra/docker-compose.prod.yml").read_text(encoding="utf-8")

    assert dev.count("driver: json-file") == 1  # postgres — the only service
    assert prod.count("driver: json-file") == 4  # postgres, api, web, proxy
    for content in (dev, prod):
        assert 'max-size: "10m"' in content
        assert 'max-file: "3"' in content


def test_lifecycle_scripts_rotate_log_and_disable_uvicorn_access_log() -> None:
    """backend-on.sh: .prev rotation before Alembic, append-only uvicorn with
    --no-access-log; docker-entrypoint.sh: --no-access-log; the frozen E2E
    third path keeps uvicorn's access log (documented duplication)."""
    on_script = (REPO_ROOT / "scripts/backend-on.sh").read_text(encoding="utf-8")
    entrypoint = (REPO_ROOT / "backend/docker-entrypoint.sh").read_text(encoding="utf-8")
    e2e_start = (REPO_ROOT / "frontend/e2e/scripts/start-api.mjs").read_text(
        encoding="utf-8"
    )

    assert 'LOG="/tmp/sparkprompt-uvicorn.log"' in on_script
    rotation = 'mv -f "$LOG" "$LOG.prev"'
    assert rotation in on_script
    assert on_script.index(rotation) < on_script.index("upgrade head"), (
        "rotation must run before this boot's Alembic output lands"
    )
    uvicorn_line = next(
        line for line in on_script.splitlines() if "uvicorn app.main:app" in line
    )
    assert "--no-access-log" in uvicorn_line
    assert '>>"$LOG"' in uvicorn_line, "uvicorn output must append, not truncate"
    alembic_line = next(
        line
        for line in on_script.splitlines()
        if "alembic.ini" in line and "upgrade head" in line
    )
    assert '>>"$LOG"' in alembic_line, "migration output must survive in $LOG"

    assert "--no-access-log" in entrypoint

    # Frozen file — intentionally NOT --no-access-log (DEPLOYMENT.md §14.1 #18).
    assert "--no-access-log" not in e2e_start
