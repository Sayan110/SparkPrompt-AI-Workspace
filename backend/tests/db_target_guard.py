"""S1 - fail-closed database-target guard for the pytest suite (Phase 5B).

Resolves the effective database target the same way the application does
(pydantic-settings precedence: real environment variable ``DATABASE_URL`` ->
local environment file ``.env`` -> the ``DEV_DATABASE_URL`` source default) and
refuses to let pytest continue when the target is protected, ambiguous, or
unparseable.

Safety rules encoded here:
- Protected names (case-insensitive): ``sparkprompt``, ``postgres``,
  ``template1``.
- An unset/blank ``DATABASE_URL`` never falls through silently: if a local env
  file exists the target is ambiguous (this guard never reads env files), and
  otherwise the application default resolves to the protected development
  database - both cases fail closed with a pointer to ``scripts/test-pytest.sh``.
- A URL that cannot be parsed, has no database name, or is not a PostgreSQL
  URL fails closed (the target cannot be proven safe).
- URL query parameters are validated against a narrow allow-list of
  connection-tuning options; anything else (``dbname``, ``host``, ``port``,
  ``user``, ...) fails closed because the driver honors those parameters over
  the validated database name (see ``ALLOWED_URL_QUERY_PARAMS``).
- Error messages carry only the database name / scheme / file path - never the
  URL, host, username, or password.

This module is import-safe: it performs no database connections and does not
import the application engine. Unit tests exercise it with constructed URLs and
mappings only.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

PROTECTED_DATABASE_NAMES = frozenset({"sparkprompt", "postgres", "template1"})
SAFE_RUNNER_HINT = "scripts/test-pytest.sh"
RESOLVED_FROM = "DATABASE_URL"

# A1 (Phase 5B.8): URL query parameters are forwarded to the driver by
# SQLAlchemy's psycopg3 dialect, where they can silently REPLACE the validated
# target. Measured with pure parsing (no connection): a URL ending in
# ".../sparkprompt_test?dbname=sparkprompt" reports database="sparkprompt_test"
# to this guard but dbname="sparkprompt" to the driver, and ?host=, ?port=,
# ?user= override the rest of the connection target. Only connection-tuning
# parameters that cannot change WHERE or AS WHOM we connect are accepted;
# matching is case-insensitive (make_url preserves key case as typed).
ALLOWED_URL_QUERY_PARAMS = frozenset(
    {
        "sslmode",
        "sslrootcert",
        "sslcert",
        "sslkey",
        "connect_timeout",
        "application_name",
        "target_session_attrs",
        "keepalives_idle",
        "keepalives_interval",
        "keepalives_count",
    }
)


class DatabaseTargetGuardError(RuntimeError):
    """Raised when pytest must refuse to run against the configured target."""


def database_name_from_url(url: str) -> str:
    """Return the database name of a PostgreSQL URL or fail closed.

    Parsing uses SQLAlchemy's own URL parser (the same one ``create_engine``
    uses), so resolution stays consistent with the application. Parsing never
    opens a connection.
    """
    from sqlalchemy.engine import make_url  # app dependency; parse-only

    raw = (url or "").strip()
    if not raw:
        raise DatabaseTargetGuardError(
            "connection URL is empty - no database target can be proven; "
            f"failing closed. Use {SAFE_RUNNER_HINT} or set DATABASE_URL to a "
            "disposable database."
        )
    try:
        parsed = make_url(raw)
    except Exception as exc:  # noqa: BLE001 - any parse failure must fail closed
        raise DatabaseTargetGuardError(
            f"connection URL cannot be parsed into a database target "
            f"({type(exc).__name__}) - failing closed. Use {SAFE_RUNNER_HINT} "
            "or set DATABASE_URL to a disposable database."
        ) from None
    backend = parsed.get_backend_name()
    if backend != "postgresql":
        raise DatabaseTargetGuardError(
            f"connection URL scheme {backend or '<none>'!r} is not postgresql - "
            f"failing closed. Use {SAFE_RUNNER_HINT}."
        )
    name = parsed.database
    if not name:
        raise DatabaseTargetGuardError(
            "connection URL resolves to no database name - failing closed. "
            f"Use {SAFE_RUNNER_HINT}."
        )
    unsafe = sorted(
        {key for key in parsed.query if key.casefold() not in ALLOWED_URL_QUERY_PARAMS}
    )
    if unsafe:
        shown = ", ".join(repr(key) for key in unsafe)
        allowed = ", ".join(sorted(ALLOWED_URL_QUERY_PARAMS))
        raise DatabaseTargetGuardError(
            f"connection URL query parameter(s) {shown} are not permitted - "
            "query parameters are passed to the driver and can override the "
            "validated database target (e.g. dbname, host, port, user). Only "
            f"[{allowed}] are accepted - failing closed. Use {SAFE_RUNNER_HINT}."
        )
    return name


def verify_database_target(url: str, *, source: str) -> str:
    """Return the database name if it is not protected; else refuse.

    Comparison is case-insensitive (PostgreSQL folds unquoted identifiers to
    lowercase, so ``SPARKPROMPT`` names the same database as ``sparkprompt``).
    """
    name = database_name_from_url(url)
    if name.casefold() in PROTECTED_DATABASE_NAMES:
        raise DatabaseTargetGuardError(
            f"refusing to run pytest against protected database {name!r} "
            f"(resolved from {source}). Use {SAFE_RUNNER_HINT} (it targets the "
            "disposable 'sparkprompt_test' database) or point DATABASE_URL at "
            "another disposable database."
        )
    return name


def _default_env_file_candidates() -> list[str]:
    """Existing env-file paths that could shadow DATABASE_URL.

    Mirrors (as a conservative superset) the application's ``env_file``
    candidates ``(".env", "../.env")`` relative to the working directory, plus
    paths relative to this file (``backend/.env``, repo ``.env``). Only file
    *existence* is checked - the guard never opens env files.
    """
    here = Path(__file__).resolve()
    candidates = (
        Path.cwd() / ".env",
        Path.cwd().parent / ".env",
        here.parents[1] / ".env",  # backend/.env
        here.parents[2] / ".env",  # repository root .env
    )
    found: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            found.append(key)
    return found


def enforce_safe_database_target(
    environ: Mapping[str, str] | None = None,
    env_files: Sequence[str | Path] | None = None,
) -> str:
    """Resolve and verify the effective database target; return its name.

    Raises ``DatabaseTargetGuardError`` (fail closed) unless the target is a
    proven non-protected PostgreSQL database name.

    ``environ``/``env_files`` are injectable for unit tests; production call
    sites (``backend/tests/conftest.py``) use the defaults: the real process
    environment and auto-detected env-file candidates. Neither argument is
    mutated.
    """
    env = os.environ if environ is None else environ
    raw = (env.get("DATABASE_URL") or "").strip()
    if not raw:
        files = (
            _default_env_file_candidates()
            if env_files is None
            else [str(path) for path in env_files]
        )
        if files:
            raise DatabaseTargetGuardError(
                "DATABASE_URL is not set but a local environment file exists "
                f"({files[0]}) that may define it - the target is ambiguous and "
                "this guard never reads env files, so it fails closed. Set "
                f"DATABASE_URL explicitly to a disposable database or run "
                f"{SAFE_RUNNER_HINT}."
            )
        from app.core.config import DEV_DATABASE_URL

        default_name = database_name_from_url(DEV_DATABASE_URL)  # fails closed
        raise DatabaseTargetGuardError(
            "DATABASE_URL is not set; pytest would fall back to the "
            f"application default database {default_name!r}, which is "
            f"protected. Refusing to run. Use {SAFE_RUNNER_HINT} or set "
            "DATABASE_URL to a disposable database."
        )
    return verify_database_target(raw, source=RESOLVED_FROM)
