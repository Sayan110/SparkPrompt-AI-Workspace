"""Authentication endpoints: signup, login, logout, session probe (Phase 4A).

Session delivery: an HttpOnly cookie (``sparkprompt_session``) carrying a
short-lived signed token — never a token in a response body, localStorage, or
sessionStorage. ``signup``/``login`` are rate limited at the router level of
their dependencies; ``logout`` is idempotent (200 even without a session) and
revokes the presented token server-side so it cannot be replayed before its
natural expiry. ``me`` is the session probe the frontend uses on boot.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, rate_limit_login, rate_limit_signup
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    revoke_session_token,
)
from app.models import User
from app.schemas.auth import AuthLoginRequest, AuthSignupRequest, UserOut
from app.schemas.common import Message
from app.services import auth as auth_service
from app.services.errors import EmailTakenError, InvalidCredentialsError

router = APIRouter()

SIGNED_OUT_MESSAGE = "Signed out."

# Phase 4E: auth event visibility. Success logs the internal UUID only;
# failures log machine-readable reason codes — never an email address, never
# a submitted password, never a token (privacy rules in app.core.logging).
logger = logging.getLogger(__name__)


def _set_session_cookie(response: Response, user_id: uuid.UUID) -> None:
    """Attach the session cookie: HttpOnly + SameSite=Lax, Secure per config."""
    settings = get_settings()
    token = create_session_token(user_id, settings.session_ttl_seconds)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.session_ttl_seconds,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


@router.post("/signup", status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit_signup)])
def signup(payload: AuthSignupRequest, response: Response, db: Session = Depends(get_db)) -> UserOut:
    try:
        user = auth_service.signup(db, payload)
    except EmailTakenError as exc:
        logger.warning("auth signup rejected", extra={"reason": "email_taken"})
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    logger.info("auth signup ok", extra={"user_id": str(user.id)})
    _set_session_cookie(response, user.id)
    return UserOut.model_validate(user)


@router.post("/login", dependencies=[Depends(rate_limit_login)])
def login(payload: AuthLoginRequest, response: Response, db: Session = Depends(get_db)) -> UserOut:
    try:
        user = auth_service.login(db, payload)
    except InvalidCredentialsError as exc:
        logger.warning("auth login rejected", extra={"reason": "invalid_credentials"})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from None
    logger.info("auth login ok", extra={"user_id": str(user.id)})
    _set_session_cookie(response, user.id)
    return UserOut.model_validate(user)


@router.post("/logout")
def logout(request: Request, response: Response) -> Message:
    # Idempotent by design: clearing a stale/expired session must never fail.
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        revoke_session_token(token)
    # Event only: the presented token is deliberately NOT logged (it would be
    # a session credential), and there is no user id without session lookup.
    logger.info("auth logout")
    _clear_session_cookie(response)
    return Message(message=SIGNED_OUT_MESSAGE)


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)
