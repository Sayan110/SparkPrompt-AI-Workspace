"""Phase 4D — production deployment configuration rules.

These tests pin the fail-closed contract: development keeps safe local
defaults (ephemeral secret, HTTP cookies, localhost CORS), while production
refuses to start without explicit SESSION_SECRET / DATABASE_URL / CORS and a
secure session cookie — always with actionable messages that never contain a
configured value. No database is required except the single readiness test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core import config_check
from app.core.config import DEV_DATABASE_URL, MIN_SESSION_SECRET_LENGTH, Settings
from app.core.security import resolve_session_secret

VALID_PRODUCTION = {
    "environment": "production",
    "database_url": "postgresql+psycopg://sp_deploy:deploy-only-pw@db.internal:5432/sparkprompt",
    "cors_origins": "https://app.example.com,https://admin.example.com",
    "session_secret": "stable-production-signing-key-0123456789abcdef",
    "session_cookie_secure": True,
}


def _production(**overrides) -> Settings:
    return Settings(**{**VALID_PRODUCTION, **overrides})


# --------------------------------------------------------------------------
# Environment model
# --------------------------------------------------------------------------


def test_development_is_the_default_environment(monkeypatch) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    settings = Settings()
    assert settings.environment == "development"
    assert settings.production_problems() == []
    settings.validate_for_startup()  # must not raise outside production


def test_unknown_environment_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="staging")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# SESSION_SECRET (Steps 4 / 25)
# --------------------------------------------------------------------------


def test_development_keeps_ephemeral_secret_behavior(monkeypatch) -> None:
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    dev = Settings(session_secret="", environment="development")
    first = resolve_session_secret(dev)
    second = resolve_session_secret(dev)
    assert first and second
    assert first != second  # ephemeral per process, as documented for 4A/4D


def test_production_missing_session_secret_fails_closed() -> None:
    settings = _production(session_secret="")
    problems = settings.production_problems()
    assert any(
        "SESSION_SECRET is required when ENVIRONMENT=production" in p for p in problems
    )
    with pytest.raises(RuntimeError, match="SESSION_SECRET is required"):
        settings.validate_for_startup()
    with pytest.raises(RuntimeError, match="SESSION_SECRET is required"):
        resolve_session_secret(settings)


def test_production_short_session_secret_is_rejected() -> None:
    problems = _production(session_secret="too-short").production_problems()
    assert any(
        "SESSION_SECRET is too short" in p and str(MIN_SESSION_SECRET_LENGTH) in p
        for p in problems
    )


# --------------------------------------------------------------------------
# Database configuration (Step 6)
# --------------------------------------------------------------------------


def test_production_rejects_development_database_default() -> None:
    problems = _production(database_url=DEV_DATABASE_URL).production_problems()
    assert any("development default is not allowed" in p for p in problems)


def test_production_rejects_development_database_password() -> None:
    problems = _production(
        database_url="postgresql+psycopg://sparkprompt:sparkprompt@db.internal:5432/sparkprompt"
    ).production_problems()
    assert any("development default password" in p for p in problems)


def test_production_rejects_database_url_without_password() -> None:
    problems = _production(
        database_url="postgresql+psycopg://sp_deploy@db.internal:5432/sparkprompt"
    ).production_problems()
    assert any("must include a password" in p for p in problems)


def test_production_rejects_non_postgres_scheme() -> None:
    problems = _production(
        database_url="sqlite:////tmp/sparkprompt.db"
    ).production_problems()
    assert any("postgresql:// scheme" in p for p in problems)


# --------------------------------------------------------------------------
# CORS (Step 7) and cookie security (Step 5)
# --------------------------------------------------------------------------


def test_production_rejects_wildcard_cors() -> None:
    problems = _production(cors_origins="*").production_problems()
    assert any("wildcard" in p for p in problems)


def test_production_requires_explicit_cors_origin() -> None:
    problems = _production(cors_origins="").production_problems()
    assert any("at least one trusted frontend origin" in p for p in problems)


def test_production_rejects_malformed_cors_origin() -> None:
    problems = _production(cors_origins="app.example.com").production_problems()
    assert any("not a bare origin" in p for p in problems)
    problems = _production(cors_origins="https://app.example.com/login").production_problems()
    assert any("not a bare origin" in p for p in problems)


def test_production_requires_secure_cookie() -> None:
    problems = _production(session_cookie_secure=False).production_problems()
    assert any("SESSION_COOKIE_SECURE=true is required" in p for p in problems)


# --------------------------------------------------------------------------
# Valid production configuration + secret hygiene
# --------------------------------------------------------------------------


def test_valid_production_configuration_passes() -> None:
    settings = _production()
    assert settings.production_problems() == []
    settings.validate_for_startup()  # does not raise
    assert resolve_session_secret(settings) == VALID_PRODUCTION["session_secret"]


def test_problem_messages_never_contain_secret_values() -> None:
    secret_marker = "UNIQUE_SESSION_SECRET_MARKER_9f8e7d6c5b4a"
    db_password_marker = "UNIQUE_DB_PASSWORD_MARKER_1a2b3c4d"
    settings = _production(
        session_secret=secret_marker,
        database_url=f"postgresql+psycopg://sp_deploy:{db_password_marker}@db.internal:5432/sparkprompt",
        session_cookie_secure=False,  # the deliberate problem
        cors_origins="*",
    )
    text = " ".join(settings.production_problems())
    assert "SESSION_COOKIE_SECURE" in text  # actionable
    assert secret_marker not in text
    assert db_password_marker not in text


# --------------------------------------------------------------------------
# Standalone validation CLI (Step 24)
# --------------------------------------------------------------------------


def test_config_check_passes_for_development(monkeypatch, capsys) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert config_check.main([]) == 0
    assert "configuration OK (environment=development)" in capsys.readouterr().out


def test_config_check_fails_for_invalid_production_without_leaking(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SESSION_SECRET", "leaky-marker-should-never-be-printed-0123456789")
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    # DATABASE_URL stays at the development default -> guaranteed failure.
    assert config_check.main([]) == 1
    captured = capsys.readouterr()
    assert "SETUP ERROR" in captured.err
    assert "SESSION_COOKIE_SECURE" in captured.err
    assert "DATABASE_URL" in captured.err
    assert "leaky-marker-should-never-be-printed-0123456789" not in captured.err
    assert "leaky-marker-should-never-be-printed-0123456789" not in captured.out


def test_config_check_require_production_blocks_development(
    monkeypatch, capsys
) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert config_check.main(["--require", "production"]) == 1
    assert "environment must be production" in capsys.readouterr().err


def test_config_check_require_production_passes_with_full_environment(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SESSION_SECRET", VALID_PRODUCTION["session_secret"])
    monkeypatch.setenv("DATABASE_URL", VALID_PRODUCTION["database_url"])
    monkeypatch.setenv("CORS_ORIGINS", VALID_PRODUCTION["cors_origins"])
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    assert config_check.main(["--require", "production"]) == 0
    assert "configuration OK (environment=production)" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Readiness endpoint (Step 17) — integration
# --------------------------------------------------------------------------


def _db_reachable() -> bool:
    try:
        from sqlalchemy import text

        from app.core.database import engine

        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 - skip cleanly when Postgres is down
        print(f"  - DB unreachable, skipping integration: {exc}")
        return False


@pytest.fixture(scope="module")
def db_ready() -> bool:
    return _db_reachable()


def _require_db(db_ready: bool) -> None:
    if not db_ready:
        pytest.skip("Postgres not reachable")


def test_readiness_reports_database_and_revision_at_head(app_client, db_ready) -> None:
    _require_db(db_ready)
    from app.core.database import expected_schema_revision

    response = app_client.get("/api/health/ready")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"database": "ok", "migrations": "ok"}
    assert body["revision"] == expected_schema_revision()
    # Liveness contract unchanged (Phase 4A/4B).
    live = app_client.get("/api/health")
    assert live.status_code == 200
    assert live.json() == {"status": "ok", "service": "sparkprompt-api"}
    # No secret-shaped keys ever appear in health payloads.
    forbidden = ("password", "secret", "token", "database_url", "session")
    assert not any(word in str(body).lower() for word in forbidden)
