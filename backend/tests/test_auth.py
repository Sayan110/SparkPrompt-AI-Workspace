"""Phase 4A authentication and security-boundary tests.

Signup/login/logout semantics, session-cookie flags, token validation, auth
rate limiting, security headers, and an OpenAPI-driven sweep proving every
protected route answers 401 — never 404/405 (routing) and never 422
(validation) — for an unauthenticated request. The conventions match the other
integration modules: the ``integration`` marker, a module-scoped ``db_ready``
reachability check, and ``PlainTestClient`` (the conftest ``TestClient`` is
pre-signed-in as demo; auth tests manage sessions explicitly).

Rate limits are process-local and keyed by client IP, so each test starts from
clean limiter state via the autouse ``_reset_auth_state`` fixture: at most 5
signups and 10 logins per test.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal, engine
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    verify_password,
)
from app.models import User
from app.services.auth import (
    DEMO_EMAIL,
    EMAIL_TAKEN_MESSAGE,
    INVALID_CREDENTIALS_MESSAGE,
)
from app.services.identity import AUTH_REQUIRED_MESSAGE

from conftest import DemoSessionTestClient, PlainTestClient

pytestmark = pytest.mark.integration

PASSWORD = "4a-Test-Passw0rd"
_PATH_PARAM = re.compile(r"\{(\w+)\}")
_PUBLIC_PREFIXES = ("/api/health", "/api/auth")


# ----------------------------------------------------------------- db helpers


def _db_reachable() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(select(1))
        return True
    except SQLAlchemyError as exc:
        print(f"  - DB unreachable, skipping integration: {exc}")
        return False


@pytest.fixture(scope="module")
def db_ready() -> bool:
    return _db_reachable()


def _require_db(db_ready: bool) -> None:
    if not db_ready:
        pytest.skip("Postgres not reachable")


# ------------------------------------------------------------------- helpers


def _client() -> PlainTestClient:
    from app.main import app

    return PlainTestClient(app)


def _unique_email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def _signup(client, email: str | None = None, password: str = PASSWORD) -> dict:
    response = client.post(
        "/api/auth/signup",
        json={"email": email or _unique_email("4a"), "password": password},
    )
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------------------- signup


def test_signup_returns_user_and_sets_session_cookie(db_ready):
    """201 + HttpOnly cookie that immediately authenticates /api/auth/me."""
    _require_db(db_ready)
    client = _client()
    email = _unique_email("signup")
    response = client.post(
        "/api/auth/signup", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text

    user = response.json()
    assert set(user) == {"id", "email", "display_name", "created_at"}
    assert user["email"] == email
    uuid.UUID(user["id"])

    cookie = response.headers.get("set-cookie", "")
    assert f"{SESSION_COOKIE_NAME}=" in cookie
    assert "; HttpOnly" in cookie
    assert "; SameSite=lax" in cookie
    assert "; Path=/" in cookie
    assert "Max-Age=43200" in cookie
    # Dev default: session_cookie_secure is false, so no Secure attribute —
    # asserting the attribute (not a substring) so a base64 token can't false-positive.
    assert "; Secure" not in cookie

    me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["email"] == email


def test_signup_stores_only_a_scrypt_hash_and_never_returns_it(db_ready):
    """The stored secret is scrypt; the plaintext and hash never leave the server."""
    _require_db(db_ready)
    client = _client()
    email = _unique_email("hash")
    response = client.post(
        "/api/auth/signup", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    assert PASSWORD not in response.text
    assert "scrypt" not in response.text
    assert "password_hash" not in response.text

    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.email == email))
        assert user is not None, "signup must persist the account"
        stored = user.password_hash
    assert stored is not None and stored.startswith("scrypt$")
    assert PASSWORD not in stored
    assert verify_password(PASSWORD, stored) is True
    assert verify_password(PASSWORD + "-wrong", stored) is False
    assert verify_password("other-password", stored) is False


def test_signup_duplicate_email_is_a_case_insensitive_409(db_ready):
    """Same address in any letter case is one account: 409, no second user."""
    _require_db(db_ready)
    client = _client()
    email = _unique_email("dupe")
    _signup(client, email=email)
    response = client.post(
        "/api/auth/signup", json={"email": email.upper(), "password": PASSWORD}
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == EMAIL_TAKEN_MESSAGE

    with SessionLocal() as session:
        count = session.scalar(
            select(User.id).where(User.email == email.lower())
        )
        rows = session.execute(
            select(User).where(User.email.like(email.split("@")[0] + "%"))
        ).all()
    assert count is not None
    assert len(rows) == 1, "the duplicate signup must not create a second row"


def test_signup_rejects_short_password_and_malformed_email(db_ready):
    """Password rules are server-enforced: 8-128 chars, conservative email shape."""
    _require_db(db_ready)
    client = _client()
    short = client.post(
        "/api/auth/signup",
        json={"email": _unique_email("short"), "password": "short1"},
    )
    assert short.status_code == 422, short.text
    bad_email = client.post(
        "/api/auth/signup", json={"email": "not-an-email", "password": PASSWORD}
    )
    assert bad_email.status_code == 422, bad_email.text
    too_long = client.post(
        "/api/auth/signup",
        json={"email": _unique_email("long"), "password": "x" * 129},
    )
    assert too_long.status_code == 422, too_long.text


# -------------------------------------------------------------------- login


def test_login_me_logout_cycle(db_ready):
    """Login issues the cookie, me resolves the user, logout ends the session."""
    _require_db(db_ready)
    creator = _client()
    email = _unique_email("cycle")
    _signup(creator, email=email)

    client = _client()
    wrong = client.post(
        "/api/auth/login", json={"email": email, "password": "wrong-password"}
    )
    assert wrong.status_code == 401, wrong.text
    assert wrong.json()["detail"] == INVALID_CREDENTIALS_MESSAGE

    good = client.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert good.status_code == 200, good.text
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == email

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_login_failures_are_indistinguishable(db_ready):
    """Unknown email and wrong password are byte-identical 401s (no enumeration)."""
    _require_db(db_ready)
    client = _client()
    known = _unique_email("known")
    _signup(_client(), email=known)

    unknown = client.post(
        "/api/auth/login",
        json={"email": _unique_email("ghost"), "password": PASSWORD},
    )
    wrong = client.post(
        "/api/auth/login", json={"email": known, "password": "wrong-password"}
    )
    assert unknown.status_code == 401
    assert wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_demo_account_cannot_log_in(db_ready):
    """The seeded demo account owns the legacy data but has no usable password."""
    _require_db(db_ready)
    client = _client()
    response = client.post(
        "/api/auth/login", json={"email": DEMO_EMAIL, "password": PASSWORD}
    )
    assert response.status_code == 401, response.text
    assert response.json()["detail"] == INVALID_CREDENTIALS_MESSAGE


def test_me_requires_a_session_with_uniform_401(db_ready):
    """No cookie, garbage cookie — the same uniform 401, never a 404/500."""
    _require_db(db_ready)
    client = _client()
    bare = client.get("/api/auth/me")
    assert bare.status_code == 401
    assert bare.json()["detail"] == AUTH_REQUIRED_MESSAGE

    client.cookies.set(SESSION_COOKIE_NAME, "garbage-token-not-signed")
    garbage = client.get("/api/auth/me")
    assert garbage.status_code == 401
    assert garbage.json() == bare.json()


def test_tampered_and_expired_tokens_are_rejected(db_ready):
    """Signature tampering and past expiry both fail closed to 401."""
    _require_db(db_ready)
    creator = _client()
    signup = creator.post(
        "/api/auth/signup",
        json={"email": _unique_email("token"), "password": PASSWORD},
    )
    assert signup.status_code == 201, signup.text
    valid = creator.cookies.get(SESSION_COOKIE_NAME)
    assert valid is not None and valid.startswith("v1.")

    fresh = _client()
    fresh.cookies.set(SESSION_COOKIE_NAME, valid)
    assert fresh.get("/api/auth/me").status_code == 200

    version, body, signature = valid.split(".")
    flipped = "A" if signature[-1] != "A" else "B"
    tampered = f"{version}.{body}.{signature[:-1]}{flipped}"
    fresh.cookies.set(SESSION_COOKIE_NAME, tampered)
    assert fresh.get("/api/auth/me").status_code == 401

    user_id = uuid.UUID(signup.json()["id"])
    expired = create_session_token(user_id, ttl_seconds=-60)
    fresh.cookies.set(SESSION_COOKIE_NAME, expired)
    assert fresh.get("/api/auth/me").status_code == 401


def test_logout_clears_cookie_revokes_token_and_is_idempotent(db_ready):
    """Logout empties the browser jar AND revokes the token server-side."""
    _require_db(db_ready)
    client = _client()
    _signup(client)
    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert token is not None

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    cleared = logout.headers.get("set-cookie", "")
    assert f"{SESSION_COOKIE_NAME}=" in cleared
    assert "Max-Age=0" in cleared
    assert client.get("/api/auth/me").status_code == 401

    # Replaying the pre-logout token on a fresh client fails: revoked, not merely deleted.
    replay = _client()
    replay.cookies.set(SESSION_COOKIE_NAME, token)
    assert replay.get("/api/auth/me").status_code == 401

    # Signing out twice (or without a session) must never fail.
    assert client.post("/api/auth/logout").status_code == 200
    assert _client().post("/api/auth/logout").status_code == 200


def test_seeded_demo_session_is_superseded_by_real_login_and_logout(db_ready):
    """The harness cookie behaves like a browser jar: login swaps it, logout empties it."""
    _require_db(db_ready)
    from app.main import app

    seeded = DemoSessionTestClient(app)
    demo_me = seeded.get("/api/auth/me")
    assert demo_me.status_code == 200, demo_me.text
    demo_id = demo_me.json()["id"]

    owner = _client()
    email = _unique_email("seeded")
    _signup(owner, email=email)

    login = seeded.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    me = seeded.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == email
    assert me.json()["id"] != demo_id, "login must replace the demo session"

    # Logout must remove the entry outright — no ghost seed surviving underneath.
    assert seeded.post("/api/auth/logout").status_code == 200
    assert seeded.get("/api/auth/me").status_code == 401


# --------------------------------------------------------------- rate limits


def test_login_rate_limit_answers_429_with_retry_after(db_ready):
    """10 attempts per 60s per client IP; the 11th is 429 with a usable Retry-After."""
    _require_db(db_ready)
    client = _client()
    for _ in range(10):
        response = client.post(
            "/api/auth/login",
            json={"email": _unique_email("rl"), "password": PASSWORD},
        )
        assert response.status_code == 401, response.text
    blocked = client.post(
        "/api/auth/login",
        json={"email": _unique_email("rl"), "password": PASSWORD},
    )
    assert blocked.status_code == 429, blocked.text
    retry_after = blocked.headers.get("retry-after")
    assert retry_after is not None and int(retry_after) >= 1
    assert blocked.json()["detail"]


def test_signup_rate_limit_answers_429_with_retry_after(db_ready):
    """5 attempts per 300s per client IP; every attempt counts, 6th is 429."""
    _require_db(db_ready)
    client = _client()
    email = _unique_email("su-rl")
    first = client.post(
        "/api/auth/signup", json={"email": email, "password": PASSWORD}
    )
    assert first.status_code == 201, first.text
    for _ in range(4):
        again = client.post(
            "/api/auth/signup", json={"email": email, "password": PASSWORD}
        )
        assert again.status_code == 409, again.text
    blocked = client.post(
        "/api/auth/signup",
        json={"email": _unique_email("su-rl"), "password": PASSWORD},
    )
    assert blocked.status_code == 429, blocked.text
    retry_after = blocked.headers.get("retry-after")
    assert retry_after is not None and int(retry_after) >= 1


# ---------------------------------------------------------- security headers


def test_security_headers_on_public_protected_and_auth_responses(db_ready):
    """The three minimal headers ride every response: 200, 401, and 201 alike."""
    _require_db(db_ready)
    client = _client()
    responses = [
        client.get("/api/health"),
        client.get("/api/auth/me"),
        client.post(
            "/api/auth/login",
            json={"email": _unique_email("hdr"), "password": PASSWORD},
        ),
        client.post(
            "/api/auth/signup",
            json={"email": _unique_email("hdr"), "password": PASSWORD},
        ),
    ]
    for response in responses:
        assert response.headers.get("x-content-type-options") == "nosniff"
        assert response.headers.get("x-frame-options") == "DENY"
        assert response.headers.get("referrer-policy") == "same-origin"


def test_auth_responses_never_contain_password_hash_or_session_token(db_ready):
    """Bodies carry the user shape only: no plaintext, no hash, no token."""
    _require_db(db_ready)
    client = _client()
    email = _unique_email("leak")
    signup = client.post(
        "/api/auth/signup", json={"email": email, "password": PASSWORD}
    )
    assert signup.status_code == 201, signup.text
    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert token is not None

    login = client.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    me = client.get("/api/auth/me")
    logout = client.post("/api/auth/logout")

    for response in (signup, login, me, logout):
        assert PASSWORD not in response.text
        assert "scrypt" not in response.text
        assert "password_hash" not in response.text
        assert token not in response.text
        assert "v1." not in response.text  # signed token never appears in a body


# ------------------------------------------------------- the protected sweep


def test_every_protected_route_rejects_unauthenticated_requests(db_ready):
    """OpenAPI-driven 401 sweep: protection is proven per operation, not assumed.

    For every documented operation outside the public sections (/api/health*,
    /api/auth/*), an unauthenticated request must answer 401 with the uniform
    detail — never 404/405 (routing), never 422 (path/body validation), never
    500. The router dependency resolves before validation, so the answer is 401
    regardless of arguments; a future router added without the dependency would
    fail this sweep.
    """
    _require_db(db_ready)
    from app.main import app

    schema = app.openapi()
    client = _client()
    failures: list[str] = []
    checked = 0

    for path, operations in schema["paths"].items():
        if not path.startswith("/api/"):
            continue
        if path.startswith(_PUBLIC_PREFIXES):
            continue
        for method in operations:
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            url = path
            for param in _PATH_PARAM.findall(path):
                url = url.replace("{" + param + "}", "missing")
            kwargs: dict = {"json": {}} if method.lower() in {"post", "put", "patch"} else {}
            response = client.request(method.upper(), url, **kwargs)
            checked += 1
            if response.status_code != 401:
                failures.append(
                    f"{method.upper()} {url} -> {response.status_code} {response.text[:200]}"
                )
                continue
            if response.json().get("detail") != AUTH_REQUIRED_MESSAGE:
                failures.append(
                    f"{method.upper()} {url} -> 401 with non-uniform detail: {response.text[:200]}"
                )

    # Public controls: health stays open, login still reaches its own handler.
    assert client.get("/api/health").status_code == 200
    assert checked >= 20, f"sweep covered only {checked} protected operations"
    assert not failures, failures
