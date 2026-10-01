"""Central application logging (Phase 4E).

One stderr handler, one level, one format — configured by the FastAPI
lifespan BEFORE startup validation so configuration and schema failures are
visible in structured form. Python standard library only: no structlog, no
OpenTelemetry, no hosted sink (explicitly out of scope for this phase).

Privacy contract (enforced by ``backend/tests/test_observability.py``):

* Log records carry whitelisted scalar fields only (:data:`SAFE_LOG_FIELDS`);
  anything else on a record is ignored by the formatters — arbitrary objects
  are never serialized into the log stream.
* Request/response bodies, cookies, ``Authorization`` headers, passwords and
  hashes, session tokens, ``SESSION_SECRET``, ``DATABASE_URL`` credentials,
  API keys, user emails, prompt text and generated AI output are never logged.
* Identity, when logged at all, is the internal user UUID — never an email,
  never a user object.
* The only request-scoped field is the correlation id (:func:`request_id_ctx`),
  which carries no identity.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

# Supported LOG_LEVEL names. Anything outside this list is rejected loudly at
# Settings validation and again here — never silently accepted.
LOG_LEVEL_NAMES: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_INVALID_LEVEL_MESSAGE = (
    "LOG_LEVEL must be one of: " + ", ".join(LOG_LEVEL_NAMES) + "."
)

# Correlation id of the request currently being processed; None outside a
# request (startup/shutdown lines simply omit it).
request_id_ctx: ContextVar[str | None] = ContextVar(
    "sparkprompt_request_id", default=None
)

# The ONLY field names allowed to travel from call sites into a formatted log
# record. Formatters emit exactly these when present as scalars and drop
# everything else, which is the mechanism that keeps secrets and arbitrary
# objects out of the log stream (Phase 4E privacy rules).
SAFE_LOG_FIELDS: frozenset[str] = frozenset(
    {
        "ai_request_id",  # internal AI request UUID (not a credential)
        "client_ip",      # socket peer address (never X-Forwarded-For raw)
        "duration_ms",
        "error_code",     # normalized error taxonomy value
        "latency_ms",
        "limiter",        # rate limiter name (login/signup), not its key
        "method",
        "model",
        "provider",       # provider id, never a key
        "reason",         # machine-readable event reason (no user data)
        "retry_after",
        "revision",       # Alembic revision (already public via readiness)
        "route",          # route template, never a query string
        "status",
        "user_id",        # internal UUID only — never an email
    }
)

_SCALAR_TYPES = (str, int, float)
_HANDLER_NAME = "sparkprompt.stderr"
_FACTORY_MARKER = "_sparkprompt_request_id_factory"


class _StderrHandler(logging.Handler):
    """Single stderr handler that resolves ``sys.stderr`` at emit time.

    Binding the stream lazily keeps writes going to the *current* stderr —
    production's real stderr, pytest's per-test capture — instead of a
    stale replaced stream from whichever moment the handler was created.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stream = sys.stderr
            stream.write(self.format(record) + "\n")
            stream.flush()
        except Exception:  # noqa: BLE001 - never let logging break a request
            self.handleError(record)


def resolve_level(level: str) -> int:
    """Resolve a LOG_LEVEL name to its ``logging`` constant.

    Case-insensitive (``info`` → INFO). Anything outside
    :data:`LOG_LEVEL_NAMES` raises ``ValueError`` naming the variable and the
    allowed values; the offending value itself is never echoed, so the message
    is safe to print to logs, CI, or container startup output.
    """
    if not isinstance(level, str):
        raise ValueError(_INVALID_LEVEL_MESSAGE)
    name = level.strip().upper()
    if name not in LOG_LEVEL_NAMES:
        raise ValueError(_INVALID_LEVEL_MESSAGE)
    return getattr(logging, name)


def _utc_timestamp(record: logging.LogRecord) -> str:
    """ISO-8601 UTC with milliseconds: ``2026-09-30T12:34:56.789Z``."""
    moment = datetime.fromtimestamp(record.created, tz=timezone.utc)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{int(record.msecs):03d}Z"


def _install_request_id_factory() -> None:
    """Give every record a ``request_id`` attribute (None outside a request).

    Attached to the process-wide record factory instead of a handler filter so
    records observed by ANY handler — pytest's capture, external tools — carry
    the same correlation id as the application handler. Installed once;
    repeated :func:`configure_logging` calls do not stack wrappers.
    """
    factory = logging.getLogRecordFactory()
    if getattr(factory, _FACTORY_MARKER, False):
        return

    def factory_with_request_id(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = factory(*args, **kwargs)
        record.request_id = request_id_ctx.get()
        return record

    setattr(factory_with_request_id, _FACTORY_MARKER, True)
    logging.setLogRecordFactory(factory_with_request_id)


def _safe_fields(record: logging.LogRecord) -> dict[str, Any]:
    """Whitelisted scalar extras on this record; everything else is dropped."""
    fields: dict[str, Any] = {}
    for name in SAFE_LOG_FIELDS:
        value = getattr(record, name, None)
        if isinstance(value, _SCALAR_TYPES):
            fields[name] = value
    return fields


class PlainFormatter(logging.Formatter):
    """Development format — human-readable, one line per record (UTC).

    ``timestamp LEVEL logger [request_id] message name=value ...`` followed by
    a traceback block only when the record carries one.
    """

    def format(self, record: logging.LogRecord) -> str:
        parts = [_utc_timestamp(record), record.levelname, record.name]
        request_id = getattr(record, "request_id", None)
        if request_id:
            parts.append(f"[{request_id}]")
        parts.append(record.getMessage())
        for name, value in _safe_fields(record).items():
            parts.append(f"{name}={value}")
        line = " ".join(parts)
        if record.exc_info and record.exc_info[0] is not None:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


class JsonFormatter(logging.Formatter):
    """Production format — exactly one valid JSON object per line.

    Messages and tracebacks containing newlines stay single-line because JSON
    escaping (``\\n``) is applied by ``json.dumps``. Only whitelisted scalar
    fields are emitted; ``request_id`` is included only when present.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _utc_timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        payload.update(_safe_fields(record))
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", *, json_format: bool = False) -> None:
    """Configure root logging: exactly one stderr handler, one level, one format.

    Idempotent: repeated calls replace the handler this module owns and never
    touch handlers installed by other tools (pytest's capture, for example).
    The FastAPI lifespan calls this before startup validation; tests call it
    directly with an explicit level/format.
    """
    resolved = resolve_level(level)
    _install_request_id_factory()
    handler = _StderrHandler()
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(JsonFormatter() if json_format else PlainFormatter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        if existing.get_name() == _HANDLER_NAME:
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(resolved)
