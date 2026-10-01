"""Request/response schemas for the authentication endpoints (Phase 4A).

Validation is enforced server-side: emails must look like ``a@b.co`` (a
conservative pattern — ``email-validator`` is not a dependency here), and
passwords must be 8–128 characters. Invalid payloads fail FastAPI's default
validation with HTTP 422 before any handler runs.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

# Conservative shape: no whitespace, one "@", a dot in the domain. Deliberately
# not RFC 5322 — this only gates account creation, the address is not emailed.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$"

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


class AuthSignupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class AuthLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class UserOut(BaseModel):
    """The authenticated user — never includes ``password_hash``."""

    id: UUID
    email: str
    display_name: str
    created_at: datetime

    model_config = {"from_attributes": True}
