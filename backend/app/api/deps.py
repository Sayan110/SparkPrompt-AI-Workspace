"""Request-level dependencies: authentication and auth-endpoint rate limits.

``get_current_user`` is the single canonical resolver for HTTP identity. It
validates the session cookie (signature → expiry → revocation → database
lookup) and stamps the user id onto the request's ``get_db`` Session, which
services later read through ``app.services.identity.resolve_owner``. It is
attached router-level in ``app.api`` so an unauthenticated request is rejected
with 401 before any body/path validation or handler code runs.
"""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.rate_limit import InMemoryRateLimiter, login_limiter, signup_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    is_session_revoked,
    verify_session_token,
)
from app.models import User
from app.services.identity import AUTH_REQUIRED_MESSAGE, stamp_identity

AUTH_REQUIRED_STATUS = 401
RATE_LIMIT_STATUS = 429

logger = logging.getLogger(__name__)


def _unauthenticated() -> HTTPException:
    """One uniform 401 — never reveals whether a session was absent, expired, or invalid."""
    return HTTPException(status_code=AUTH_REQUIRED_STATUS, detail=AUTH_REQUIRED_MESSAGE)


def _log_auth_failure(request: Request, reason: str) -> None:
    """Operators see WHY a 401 happened; the client still gets the uniform body.

    Reason values only — no email, no cookie, no token, no Authorization
    header (Phase 4E privacy rules). ``missing_cookie`` is the normal
    anonymous case and stays INFO; every other reason signals a tampered,
    expired, revoked, or deleted session and logs at WARNING.
    """
    level = logging.INFO if reason == "missing_cookie" else logging.WARNING
    client = request.client
    logger.log(
        level,
        "auth rejected",
        extra={
            "reason": reason,
            "client_ip": client.host if client is not None and client.host else "unknown",
        },
    )


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        _log_auth_failure(request, "missing_cookie")
        raise _unauthenticated()
    user_id = verify_session_token(token)
    if user_id is None:
        _log_auth_failure(request, "invalid_token")
        raise _unauthenticated()
    if is_session_revoked(token):
        _log_auth_failure(request, "revoked")
        raise _unauthenticated()
    user = db.get(User, user_id)
    if user is None:
        _log_auth_failure(request, "user_not_found")
        raise _unauthenticated()
    stamp_identity(db, user.id)
    # Phase 4E: expose ONLY the internal UUID for the access log — never the
    # user object and never the email. Anonymous requests never set it, so the
    # middleware simply omits user_id. Authentication semantics are unchanged.
    request.state.user_id = user.id
    return user


def _client_key(request: Request) -> str:
    # Socket peer address only — X-Forwarded-For is client-controlled (see rate_limit docs).
    client = request.client
    return client.host if client is not None and client.host else "unknown"


def _enforce_rate_limit(
    limiter: InMemoryRateLimiter, request: Request, detail: str, limiter_name: str
) -> None:
    allowed, retry_after = limiter.hit(_client_key(request))
    if not allowed:
        # Phase 4E: blocked attempts are abuse signals — log limiter name,
        # socket peer address, and wait time. The bucket key IS the client IP
        # (already approved for logging); no identity data beyond that.
        logger.warning(
            "rate limit exceeded",
            extra={
                "limiter": limiter_name,
                "client_ip": _client_key(request),
                "retry_after": int(retry_after),
            },
        )
        raise HTTPException(
            status_code=RATE_LIMIT_STATUS,
            detail=detail,
            headers={"Retry-After": str(retry_after)},
        )


def rate_limit_login(request: Request) -> None:
    _enforce_rate_limit(
        login_limiter,
        request,
        "Too many login attempts. Please wait a moment and try again.",
        "login",
    )


def rate_limit_signup(request: Request) -> None:
    _enforce_rate_limit(
        signup_limiter,
        request,
        "Too many signup attempts. Please wait a moment and try again.",
        "signup",
    )
