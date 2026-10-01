"""The single canonical request identity for service-layer ownership checks.

FastAPI's dependency cache guarantees that the auth dependency and the route
handler share one per-request ``get_db`` Session, so stamping a value into
``db.info`` is visible to every service call made during that request (the
async dependency stamps before any sync endpoint thread hop). Services call
:func:`resolve_owner` instead of guessing an identity — there is exactly one
resolution path, and it fails closed when no identity was stamped.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import User
from app.services.errors import NotAuthenticatedError

IDENTITY_KEY = "sparkprompt.auth_user_id"

AUTH_REQUIRED_MESSAGE = "Authentication required."


def stamp_identity(db: Session, user_id: uuid.UUID) -> None:
    """Attach the authenticated user id to this request's session."""
    db.info[IDENTITY_KEY] = user_id


def resolve_owner(db: Session) -> User:
    """Return the authenticated owner for this request or refuse to operate.

    Fail-closed: no stamped identity (direct service call, missing/expired
    session, deleted user) raises :class:`NotAuthenticatedError` → HTTP 401.
    There is no fallback to the legacy demo workspace — an unauthenticated
    code path must never read or write user data.
    """
    user_id = db.info.get(IDENTITY_KEY)
    if not isinstance(user_id, uuid.UUID):
        raise NotAuthenticatedError(AUTH_REQUIRED_MESSAGE)
    user = db.get(User, user_id)
    if user is None:
        raise NotAuthenticatedError(AUTH_REQUIRED_MESSAGE)
    return user
