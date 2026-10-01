"""Password hashing and session-token primitives (Phase 4A).

Built only on the Python standard library: ``hashlib.scrypt`` (a memory-hard
KDF) for password hashing and ``hmac``/SHA-256 for the signed session token.
No third-party crypto package is introduced, and no cryptographic scheme is
invented — scrypt and HMAC-SHA256 are the standard primitives, wrapped here
with explicit serialization and constant-time comparisons.

Session model: a short-lived signed token ``v1.<payload>.<signature>`` whose
payload carries only ``{"sub": user_id, "exp": epoch}`` (no PII). The token is
delivered in an HttpOnly cookie and is re-verified on every request, including
a server-side database lookup by the auth dependency — the cookie proves
possession of a signed statement, the database supplies the identity.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
import uuid
from functools import lru_cache

from app.core.config import get_settings

SESSION_COOKIE_NAME = "sparkprompt_session"
TOKEN_VERSION = "v1"

# scrypt parameters: N=2**14, r=8, p=1 — ~45ms/hash on this machine, 16 MiB
# working set per call. Salt is a fresh 16 random bytes per password.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_SALT_BYTES = 16
_SCRYPT_MAXMEM = 64 * 1024 * 1024

# Logout revocation set: token-hash -> expiry epoch. Process-local, bounded,
# pruned on write (documented in 4A; a shared store is a 4D/deploy item).
_MAX_REVOKED_TOKENS = 4096
_revoked_tokens: dict[str, float] = {}


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    """Hash a password as ``scrypt$N$r$p$salt$hash`` (base64url fields)."""
    salt = secrets.token_bytes(_SCRYPT_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )
    return (
        f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}"
        f"${_b64encode(salt)}${_b64encode(digest)}"
    )


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time password check against a stored hash; malformed input fails closed."""
    if not stored:
        return False
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt, expected = _b64decode(parts[4]), _b64decode(parts[5])
    except (ValueError, binascii.Error):
        return False
    # Bound the work a malformed row could ask for.
    if n < 2 or n > 2**20 or r < 1 or r > 32 or p < 1 or p > 16 or not expected:
        return False
    try:
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=_SCRYPT_MAXMEM,
        )
    except (ValueError, OverflowError):
        return False
    return hmac.compare_digest(digest, expected)


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    """A throwaway hash so unknown-email logins still pay the scrypt cost.

    Login timing must not reveal whether an email exists: the caller runs a
    real verification against this hash and fails with the same message.
    """
    return hash_password(secrets.token_urlsafe(24))


def resolve_session_secret(settings) -> str:
    """Signing key: SESSION_SECRET when configured, else ephemeral per process.

    Development: an ephemeral secret keeps keys out of the codebase entirely;
    sessions then die with the process (acceptable for local dev — documented
    for 4D).

    Production (Phase 4D): fail closed. Generating a fresh secret here would
    silently invalidate every existing session on restart, so a missing
    SESSION_SECRET is an error instead of a fallback. The token format
    (``v1.<payload>.<signature>``) is unchanged.
    """
    configured = settings.session_secret.strip()
    if configured:
        return configured
    if settings.is_production:
        raise RuntimeError(
            "SESSION_SECRET is required when ENVIRONMENT=production; "
            "refusing to generate an ephemeral secret."
        )
    return secrets.token_urlsafe(32)


@lru_cache(maxsize=1)
def _session_secret() -> str:
    return resolve_session_secret(get_settings())


def create_session_token(user_id: uuid.UUID, ttl_seconds: int | None = None) -> str:
    settings = get_settings()
    ttl = int(settings.session_ttl_seconds if ttl_seconds is None else ttl_seconds)
    payload = {"sub": str(user_id), "exp": int(time.time()) + ttl}
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(
        _session_secret().encode("utf-8"),
        f"{TOKEN_VERSION}.{body}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{TOKEN_VERSION}.{body}.{_b64encode(signature)}"


def parse_session_token(token: str) -> tuple[uuid.UUID, int] | None:
    """Signature-verified payload of a token; expiry is NOT checked here."""
    try:
        version, body, signature = token.split(".")
        if version != TOKEN_VERSION:
            return None
        expected = hmac.new(
            _session_secret().encode("utf-8"),
            f"{TOKEN_VERSION}.{body}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(_b64encode(expected), signature):
            return None
        payload = json.loads(_b64decode(body))
        return uuid.UUID(payload["sub"]), int(payload["exp"])
    except (ValueError, KeyError, TypeError, binascii.Error, json.JSONDecodeError):
        return None


def verify_session_token(token: str) -> uuid.UUID | None:
    """Signed + unexpired token → user id; anything else fails closed to None."""
    parsed = parse_session_token(token)
    if parsed is None:
        return None
    user_id, expires_at = parsed
    if expires_at < time.time():
        return None
    return user_id


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def revoke_session_token(token: str) -> bool:
    """Remember a token until its natural expiry (logout). Signature-gated."""
    parsed = parse_session_token(token)
    if parsed is None:
        return False
    now = time.time()
    if len(_revoked_tokens) >= _MAX_REVOKED_TOKENS:
        expired = [key for key, exp in _revoked_tokens.items() if exp < now]
        for key in expired:
            del _revoked_tokens[key]
        while len(_revoked_tokens) >= _MAX_REVOKED_TOKENS:
            # Insertion order ≈ issue order ≈ expiry order (fixed TTL).
            del _revoked_tokens[next(iter(_revoked_tokens))]
    _revoked_tokens[_token_key(token)] = parsed[1]
    return True


def is_session_revoked(token: str) -> bool:
    key = _token_key(token)
    expires_at = _revoked_tokens.get(key)
    if expires_at is None:
        return False
    if expires_at < time.time():
        del _revoked_tokens[key]  # token is expired anyway; drop the bookkeeping
        return False
    return True


def clear_revocations() -> None:
    """Drop all remembered revocations (test fixture hook, called between tests).

    Production never calls this: revocation entries retire on their own once
    the token they remember would have expired anyway.
    """
    _revoked_tokens.clear()
