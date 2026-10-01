from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.logging import LOG_LEVEL_NAMES

# Development defaults. These are convenient for local work and MUST never be
# accepted in production (Phase 4D fail-closed rules) — production values come
# from the environment / secret infrastructure, never from these constants.
DEV_DATABASE_URL = "postgresql+psycopg://sparkprompt:sparkprompt@127.0.0.1:5432/sparkprompt"
DEV_DATABASE_PASSWORD = "sparkprompt"
MIN_SESSION_SECRET_LENGTH = 32

EnvironmentName = Literal["development", "test", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Phase 4D: explicit environment model. An unknown value fails Pydantic
    # validation at construction; production-specific rules are enforced by
    # production_problems() / validate_for_startup() (see below).
    environment: EnvironmentName = "development"

    # Phase 4E: central log level (default INFO). Case-insensitive names are
    # normalized; anything outside LOG_LEVEL_NAMES fails Pydantic validation at
    # construction with a message naming the variable and the allowed values —
    # invalid values are rejected loudly, never silently accepted. The value
    # itself is not a secret (same precedent as ENVIRONMENT).
    log_level: str = "INFO"

    database_url: str = DEV_DATABASE_URL
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = "http://127.0.0.1:3000,http://localhost:3000"

    # Session / authentication (Phase 4A)
    # session_secret: signing key for session tokens. Empty = ephemeral
    # per-process secret (sessions die with the process; fine for local dev).
    # Set SESSION_SECRET for sessions that must survive restarts — never commit it.
    # Production requires an explicit secret (4D fail-closed rule).
    session_secret: str = ""
    session_ttl_seconds: int = 43200  # 12 hours: cookie Max-Age and token expiry
    # session_cookie_secure: send the cookie only over HTTPS. Keep false for
    # local http:// development; production requires true (4D fail-closed rule).
    session_cookie_secure: bool = False

    # AI Gateway
    ai_default_provider: str = "gemini"
    ai_timeout_seconds: float = 60.0

    # Google Gemini
    gemini_api_key: str = ""
    gemini_default_model: str = ""

    # NVIDIA NIM
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integration.api.nvidia.com"
    nvidia_default_model: str = ""

    # Ollama (local)
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_default_model: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in LOG_LEVEL_NAMES:
            # Names the variable and the fix; the invalid value is not echoed
            # by this message (the same no-values rule as production_problems).
            raise ValueError(
                "LOG_LEVEL must be one of: " + ", ".join(LOG_LEVEL_NAMES) + "."
            )
        return normalized

    def production_problems(self) -> list[str]:
        """Actionable configuration problems; empty unless environment=production.

        Messages name the offending VARIABLE and the fix. They never include a
        configured value, so a validation failure can be printed to logs or CI
        output without leaking secrets (Phase 4D Step 24/26).
        """
        if not self.is_production:
            return []
        problems: list[str] = []

        # SESSION_SECRET — required and stable; a missing secret would otherwise
        # fall back to an ephemeral key and silently invalidate every session
        # on restart (4D Step 4).
        secret = self.session_secret.strip()
        if not secret:
            problems.append(
                "SESSION_SECRET is required when ENVIRONMENT=production "
                "(set a stable random value of at least "
                f"{MIN_SESSION_SECRET_LENGTH} characters)."
            )
        elif len(secret) < MIN_SESSION_SECRET_LENGTH:
            problems.append(
                "SESSION_SECRET is too short when ENVIRONMENT=production "
                f"(minimum {MIN_SESSION_SECRET_LENGTH} characters)."
            )

        # DATABASE_URL — the development default (and the development default
        # password) must never back a production deployment (4D Step 6).
        raw = self.database_url.strip()
        if not raw or raw == DEV_DATABASE_URL:
            problems.append(
                "DATABASE_URL must be set explicitly when ENVIRONMENT=production; "
                "the development default is not allowed."
            )
        else:
            try:
                parsed = urlparse(raw)
            except ValueError:
                problems.append("DATABASE_URL is not a parseable connection string.")
                parsed = None
            if parsed is not None:
                if not parsed.scheme.startswith(("postgresql", "postgres")):
                    problems.append(
                        "DATABASE_URL must use a postgresql:// scheme when "
                        "ENVIRONMENT=production."
                    )
                if not parsed.netloc:
                    problems.append(
                        "DATABASE_URL is missing a host when ENVIRONMENT=production."
                    )
                password = parsed.password
                if not password:
                    problems.append(
                        "DATABASE_URL must include a password when "
                        "ENVIRONMENT=production."
                    )
                elif password == DEV_DATABASE_PASSWORD:
                    problems.append(
                        "DATABASE_URL uses the development default password when "
                        "ENVIRONMENT=production; use a dedicated credential."
                    )

        # CORS — explicit trusted origins only. Credentialed wildcard CORS is
        # never acceptable in production (4D Step 7).
        origins = self.cors_origin_list
        if not origins:
            problems.append(
                "CORS_ORIGINS must list at least one trusted frontend origin "
                "when ENVIRONMENT=production."
            )
        for origin in origins:
            if origin == "*":
                problems.append(
                    "CORS_ORIGINS must not contain a wildcard (*) when "
                    "ENVIRONMENT=production (credentialed CORS)."
                )
                continue
            parsed = urlparse(origin)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.netloc
                or parsed.path not in ("", "/")
                or parsed.params
                or parsed.query
                or parsed.fragment
                or " " in origin
            ):
                problems.append(
                    "CORS_ORIGINS contains an entry that is not a bare "
                    f"origin (scheme://host[:port]): {origin!r}."
                )

        # Cookies — production serves over HTTPS, so the Secure flag is
        # mandatory (4D Step 5).
        if not self.session_cookie_secure:
            problems.append(
                "SESSION_COOKIE_SECURE=true is required when ENVIRONMENT=production."
            )

        return problems

    def validate_for_startup(self) -> None:
        """Fail fast (fail closed) on invalid production configuration."""
        problems = self.production_problems()
        if problems:
            raise RuntimeError(
                "Invalid production configuration: " + " ".join(problems)
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
