"""Unit tests for the S1 fail-closed database-target guard (Phase 5B).

These tests construct URLs and environment mappings in memory only. They never
import the application engine, never build a SQLAlchemy engine, and never open
a PostgreSQL connection.
"""

from __future__ import annotations

import pytest
from db_target_guard import (
    DatabaseTargetGuardError,
    database_name_from_url,
    enforce_safe_database_target,
    verify_database_target,
)

# Synthetic credentials: used only to prove they can never leak into messages.
SYNTHETIC_PASSWORD = "Sup3rSecret!"


def _url(name: str) -> str:
    return (
        f"postgresql+psycopg://tester:{SYNTHETIC_PASSWORD}"
        f"@127.0.0.1:5432/{name}"
    )


def _assert_no_url_leak(message: str) -> None:
    assert SYNTHETIC_PASSWORD not in message, "password leaked into a message"
    assert "://" not in message, "URL leaked into a message"
    assert "127.0.0.1" not in message, "host leaked into a message"
    assert "tester" not in message, "username leaked into a message"


@pytest.mark.parametrize(
    "name",
    ["sparkprompt", "SPARKPROMPT", "Postgres", "POSTGRES", "template1", "Template1"],
)
def test_protected_names_refused_case_insensitively(name: str) -> None:
    with pytest.raises(DatabaseTargetGuardError) as excinfo:
        verify_database_target(_url(name), source="DATABASE_URL")
    message = str(excinfo.value)
    assert name.casefold() in message.casefold()
    assert "protected database" in message
    assert "scripts/test-pytest.sh" in message
    _assert_no_url_leak(message)


@pytest.mark.parametrize(
    "name",
    ["sparkprompt_test", "sparkprompt_e2e", "sparkprompt_mig_ab12cd34", "other_db"],
)
def test_disposable_names_are_accepted(name: str) -> None:
    assert verify_database_target(_url(name), source="DATABASE_URL") == name


@pytest.mark.parametrize(
    "bad_url",
    [
        "",
        "   ",
        "not-a-url",
        "://",
        "postgresql+psycopg://user@host",  # no database name
        "postgresql+psycopg://user@host/",  # empty database name
        "sqlite:///tmp/x.db",  # non-postgreSQL scheme
        "mysql://user@host/db",  # non-postgreSQL scheme
    ],
)
def test_unparseable_or_unprovable_targets_fail_closed(bad_url: str) -> None:
    with pytest.raises(DatabaseTargetGuardError):
        verify_database_target(bad_url, source="DATABASE_URL")


def test_database_name_parsed_without_credentials_in_result() -> None:
    name = database_name_from_url(_url("sparkprompt_test"))
    assert name == "sparkprompt_test"
    assert SYNTHETIC_PASSWORD not in name


def test_unset_database_url_without_env_files_names_protected_default() -> None:
    with pytest.raises(DatabaseTargetGuardError) as excinfo:
        enforce_safe_database_target(environ={}, env_files=[])
    message = str(excinfo.value)
    assert "scripts/test-pytest.sh" in message
    assert "sparkprompt" in message  # names the protected application default
    _assert_no_url_leak(message)


def test_unset_database_url_with_env_file_is_ambiguous_and_fails_closed() -> None:
    with pytest.raises(DatabaseTargetGuardError) as excinfo:
        enforce_safe_database_target(environ={}, env_files=["/x/y/.env"])
    message = str(excinfo.value)
    assert "ambiguous" in message
    assert "scripts/test-pytest.sh" in message
    _assert_no_url_leak(message)


def test_protected_target_from_environment_refused_with_source() -> None:
    environ = {"DATABASE_URL": _url("sparkprompt")}
    with pytest.raises(DatabaseTargetGuardError) as excinfo:
        enforce_safe_database_target(environ=environ, env_files=[])
    assert "DATABASE_URL" in str(excinfo.value)
    assert environ == {"DATABASE_URL": _url("sparkprompt")}, "input was mutated"


def test_whitespace_database_url_treated_as_unset() -> None:
    with pytest.raises(DatabaseTargetGuardError):
        enforce_safe_database_target(environ={"DATABASE_URL": "   "}, env_files=[])


def test_disposable_target_from_environment_accepted() -> None:
    environ = {"DATABASE_URL": _url("sparkprompt_test")}
    assert (
        enforce_safe_database_target(environ=environ, env_files=[])
        == "sparkprompt_test"
    )
    assert environ == {"DATABASE_URL": _url("sparkprompt_test")}, "input was mutated"


# --- A1 (Phase 5B.8): query-parameter target confusion ------------------------
# Evidence gathered BEFORE the guard fix: SQLAlchemy's URL parser reports the
# validated database name from the path, while the psycopg3 dialect hands URL
# query parameters to the driver, where ``dbname`` REPLACES the validated name
# (and ``host``/``port``/``user`` replace the rest). Pure parsing only - no
# engine is built and no connection is ever opened.


def test_query_parameter_override_evidence_at_dialect_level() -> None:
    from sqlalchemy.dialects.postgresql.psycopg import (
        dialect as psycopg_dialect_cls,
    )
    from sqlalchemy.engine import make_url

    url = make_url(_url("sparkprompt_test") + "?dbname=sparkprompt")
    assert url.database == "sparkprompt_test"  # what the guard validates
    _args, kwargs = psycopg_dialect_cls().create_connect_args(url)
    assert kwargs["dbname"] == "sparkprompt"  # what the driver would connect to


@pytest.mark.parametrize(
    "query",
    [
        "dbname=sparkprompt",
        "DbName=sparkprompt",
        "host=evil.internal",
        "port=6432",
        "user=other",
        "password=hunter2",
        "service=prod",
        "options=-c%20search_path=public",
    ],
)
def test_unsafe_query_parameters_rejected_fail_closed(query: str) -> None:
    key = query.split("=", 1)[0]
    with pytest.raises(DatabaseTargetGuardError) as excinfo:
        verify_database_target(
            _url("sparkprompt_test") + "?" + query, source="DATABASE_URL"
        )
    message = str(excinfo.value)
    assert "query parameter" in message
    assert repr(key) in message, "the offending parameter must be named"
    assert "scripts/test-pytest.sh" in message
    _assert_no_url_leak(message)


@pytest.mark.parametrize(
    "query",
    [
        "sslmode=prefer",
        "SSLMode=prefer",  # allow-list matching is case-insensitive
        "sslrootcert=/x/y.pem",
        "connect_timeout=5",
        "application_name=pytest",
        "target_session_attrs=any",
        "keepalives_idle=10",
    ],
)
def test_connection_tuning_query_parameters_remain_supported(query: str) -> None:
    assert (
        verify_database_target(
            _url("sparkprompt_test") + "?" + query, source="DATABASE_URL"
        )
        == "sparkprompt_test"
    )


def test_empty_query_string_is_harmless() -> None:
    assert (
        verify_database_target(_url("sparkprompt_test") + "?", source="DATABASE_URL")
        == "sparkprompt_test"
    )


def test_protected_name_still_refused_with_allowed_query() -> None:
    with pytest.raises(DatabaseTargetGuardError) as excinfo:
        verify_database_target(
            _url("sparkprompt") + "?sslmode=prefer", source="DATABASE_URL"
        )
    message = str(excinfo.value)
    assert "protected database" in message
    _assert_no_url_leak(message)


def test_enforce_path_rejects_unsafe_query_parameter() -> None:
    raw = _url("sparkprompt_test") + "?host=evil.internal"
    environ = {"DATABASE_URL": raw}
    with pytest.raises(DatabaseTargetGuardError):
        enforce_safe_database_target(environ=environ, env_files=[])
    assert environ == {"DATABASE_URL": raw}, "input was mutated"
