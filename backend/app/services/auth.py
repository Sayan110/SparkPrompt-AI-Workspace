"""Signup and login domain logic (Phase 4A).

Password rules live here and in the schema layer: signup enforces the server
minimum (8–128 characters, see ``app.schemas.auth``), and only a scrypt hash
is ever stored — plaintext and hashes never leave this module in a response.
Unknown emails still run one real scrypt verification against a dummy hash so
login timing does not reveal which addresses have accounts.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import dummy_password_hash, hash_password, verify_password
from app.models import User
from app.schemas.auth import AuthLoginRequest, AuthSignupRequest
from app.services.demo_user import get_or_create_default_project
from app.services.errors import EmailTakenError, InvalidCredentialsError

INVALID_CREDENTIALS_MESSAGE = "Invalid email or password."
EMAIL_TAKEN_MESSAGE = "An account with this email already exists."

_SPLITTERS = re.compile(r"[._\-+]+")

# The canonical demo account keeps password_hash NULL: it owns all seeded data
# but can never authenticate (login would need a matching hash).
DEMO_EMAIL = "demo@sparkprompt.local"


def normalize_email(email: str) -> str:
    """Emails are stored and compared case-insensitively (lowercase, trimmed)."""
    return email.strip().lower()


def display_name_from_email(email: str) -> str:
    """Derive a display name from the local part: ``jane.doe@x`` → ``Jane Doe``."""
    local = email.split("@", 1)[0]
    words = [word for word in _SPLITTERS.split(local) if word]
    name = " ".join(word[:1].upper() + word[1:] for word in words)
    return (name or "SparkPrompt user")[:120]


def signup(db: Session, payload: AuthSignupRequest) -> User:
    """Create an account with its own default project, then commit."""
    email = normalize_email(payload.email)
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise EmailTakenError(EMAIL_TAKEN_MESSAGE)
    user = User(
        email=email,
        display_name=display_name_from_email(email),
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.flush()
    # Every account starts with a default project, mirroring the demo workspace.
    get_or_create_default_project(db, user)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent signup won the unique-email race — same 409 as above.
        db.rollback()
        raise EmailTakenError(EMAIL_TAKEN_MESSAGE) from None
    db.refresh(user)
    return user


def login(db: Session, payload: AuthLoginRequest) -> User:
    """Authenticate an email/password pair; any failure is one indistinguishable 401."""
    email = normalize_email(payload.email)
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        # Equalize timing against a real verification (no user enumeration).
        verify_password(payload.password, dummy_password_hash())
        raise InvalidCredentialsError(INVALID_CREDENTIALS_MESSAGE)
    if not verify_password(payload.password, user.password_hash):
        raise InvalidCredentialsError(INVALID_CREDENTIALS_MESSAGE)
    return user
