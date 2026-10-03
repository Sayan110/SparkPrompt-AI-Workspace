#!/usr/bin/env python3
"""S3 - pytest final-summary parser (Phase 5B).

Pure standard library, side-effect free: reads a pytest log (CLI) or a string
(library function) and returns the counts from pytest's FINAL result line.

Design rules:
- Only lines carrying a run duration (``in 12.34s`` / ``in 1:23.45s``) are
  candidates, so intermediate detail lines (``FAILED ... assert 3 passed == 4``)
  or interrupts can never be mistaken for the run summary.
- The last matching line wins (pytest prints the result line last).
- ``no tests ran`` is a valid summary with zero counts.
- No match returns ``None`` - callers MUST treat that as a failure, never as a
  pass (a missing summary must not silently look green).

CLI:
    pytest_summary.py <pytest-log-file>
    exit 0  -> one machine-readable line printed:
               ``parsed=1 passed=682 failed=0 skipped=0 errors=0 xfailed=0
               xpassed=0 deselected=0 warnings=0 rerun=0``
    exit 1  -> no parseable summary (unparseable or unreadable log)
    exit 2  -> usage error
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, fields
from pathlib import Path

_DURATION_RE = re.compile(r"\bin\s+[0-9:.]+s\b")
_COUNT_RE = re.compile(
    r"(\d+)\s+(passed|failed|skipped|errors?|xfailed|xpassed|deselected|"
    r"warnings?|reruns?)\b",
    re.IGNORECASE,
)
_NO_TESTS_RE = re.compile(r"\bno tests ran\b", re.IGNORECASE)

# regex word -> PytestSummary field
_WORD_TO_FIELD = {
    "passed": "passed",
    "failed": "failed",
    "skipped": "skipped",
    "error": "errors",
    "errors": "errors",
    "xfailed": "xfailed",
    "xpassed": "xpassed",
    "deselected": "deselected",
    "warning": "warnings",
    "warnings": "warnings",
    "rerun": "rerun",
    "reruns": "rerun",
}


@dataclass(frozen=True)
class PytestSummary:
    """Counts parsed from pytest's final result line (missing keys = 0)."""

    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    xfailed: int = 0
    xpassed: int = 0
    deselected: int = 0
    warnings: int = 0
    rerun: int = 0

    def as_line(self) -> str:
        """Machine-readable single line for the shell gate."""
        parts = ["parsed=1"]
        parts.extend(
            f"{field.name}={getattr(self, field.name)}" for field in fields(self)
        )
        return " ".join(parts)


def parse_pytest_summary(output: str) -> PytestSummary | None:
    """Parse pytest's final summary line, or return ``None`` if absent."""
    if not isinstance(output, str):
        return None
    for line in reversed(output.splitlines()):
        if not _DURATION_RE.search(line):
            continue
        if _NO_TESTS_RE.search(line):
            return PytestSummary()
        counts: dict[str, int] = {}
        for raw_number, raw_word in _COUNT_RE.findall(line):
            field = _WORD_TO_FIELD[raw_word.lower()]
            counts[field] = counts.get(field, 0) + int(raw_number)
        if counts:
            return PytestSummary(
                **{
                    field.name: counts.get(field.name, 0)
                    for field in fields(PytestSummary)
                }
            )
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: pytest_summary.py <pytest-log-file>", file=sys.stderr)
        return 2
    try:
        text = Path(argv[1]).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"cannot read log file: {exc}", file=sys.stderr)
        return 1
    summary = parse_pytest_summary(text)
    if summary is None:
        return 1
    print(summary.as_line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
