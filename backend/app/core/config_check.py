"""Standalone deployment configuration validation (Phase 4D, Step 24).

Usage::

    python -m app.core.config_check                  # validate current env
    python -m app.core.config_check --require production

Exit codes: ``0`` configuration is valid, ``1`` one or more problems.

Output names the offending VARIABLE and the fix — never a configured value —
so the check is safe to run in CI, logs, or container startup without leaking
secrets (Phase 4D Steps 24/26). This module deliberately imports only
``app.core.config``: it must be runnable before the database or FastAPI exist.
"""

from __future__ import annotations

import argparse
import sys

from app.core.config import EnvironmentName, Settings

_ENVIRONMENTS: tuple[EnvironmentName, ...] = ("development", "test", "production")


def collect_problems(settings: Settings, require: str | None = None) -> list[str]:
    """All actionable problems for ``settings`` (empty means valid)."""
    problems: list[str] = []
    if require is not None and settings.environment != require:
        problems.append(
            f"environment must be {require} for this deployment "
            f"(set ENVIRONMENT={require})."
        )
    problems.extend(settings.production_problems())
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.core.config_check",
        description="Validate SparkPrompt deployment configuration without "
        "printing secret values.",
    )
    parser.add_argument(
        "--require",
        choices=_ENVIRONMENTS,
        default=None,
        help="Fail unless the active environment matches this value "
        "(production images use --require production).",
    )
    args = parser.parse_args(argv)

    try:
        settings = Settings()
    except Exception as exc:  # invalid ENVIRONMENT value, bad types, ...
        # Pydantic's message names the field and the bad value; environment
        # names and types are not secrets, but a secret's VALUE never appears
        # in pydantic's validation output for unrelated fields.
        print(f"SETUP ERROR: configuration could not be loaded: {exc}", file=sys.stderr)
        return 1

    problems = collect_problems(settings, args.require)
    if problems:
        for problem in problems:
            print(f"SETUP ERROR: {problem}", file=sys.stderr)
        return 1

    print(f"configuration OK (environment={settings.environment})")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess-free main()
    raise SystemExit(main())
