"""Bounded line-level text diff (Phase 3F).

A pure ``difflib.SequenceMatcher`` over lines (``autojunk=True``, matching the
Python standard) wrapped with hard bounds so arbitrarily large outputs never
blow up the response:

* each side is capped at :data:`MAX_DIFF_CHARS` before diffing,
* each diff line is capped at :data:`MAX_DIFF_LINE_LENGTH`,
* at most :data:`MAX_DIFF_HUNKS` entries are returned.

Lengths (``length_a`` / ``length_b`` / ``length_delta``) ALWAYS report the
original, uncapped texts — the numbers stay factual even when ``truncated`` is
True. The module is stdlib-only by design (import isolation).
"""

from __future__ import annotations

import difflib

from app.comparison.types import (
    MAX_DIFF_CHARS,
    MAX_DIFF_HUNKS,
    MAX_DIFF_LINE_LENGTH,
    DiffLine,
    OutputSummary,
)

_CONTEXT_LINES = 2
"""Context lines shown around each change block (bounded by MAX_DIFF_HUNKS)."""


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) > limit:
        return text[:limit], True
    return text, False


def _cap_line(line: str) -> str:
    return line if len(line) <= MAX_DIFF_LINE_LENGTH else line[:MAX_DIFF_LINE_LENGTH]


def _common_prefix(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


def _common_suffix(left: str, right: str) -> int:
    count = 0
    i, j = len(left) - 1, len(right) - 1
    while i >= 0 and j >= 0 and left[i] == right[j]:
        count += 1
        i -= 1
        j -= 1
    return count


def line_diff(left: str, right: str) -> OutputSummary:
    """Compare two texts and return a bounded, factual OutputSummary."""
    length_a = len(left)
    length_b = len(right)
    identical = left == right

    left_capped, left_cut = _truncate(left, MAX_DIFF_CHARS)
    right_capped, right_cut = _truncate(right, MAX_DIFF_CHARS)
    truncated = left_cut or right_cut

    left_lines = [_cap_line(line) for line in left_capped.splitlines()]
    right_lines = [_cap_line(line) for line in right_capped.splitlines()]

    added = removed = unchanged = 0
    entries: list[DiffLine] = []

    matcher = difflib.SequenceMatcher(None, left_lines, right_lines, autojunk=True)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            unchanged += i2 - i1
            for k in range(i1, min(i2, i1 + _CONTEXT_LINES)):
                entries.append(
                    DiffLine(kind="context", left_line=left_lines[k], right_line=left_lines[k])
                )
        elif tag == "delete":
            removed += i2 - i1
            for k in range(i1, i2):
                entries.append(DiffLine(kind="removed", left_line=left_lines[k], right_line=None))
        elif tag == "insert":
            added += j2 - j1
            for k in range(j1, j2):
                entries.append(DiffLine(kind="added", left_line=None, right_line=right_lines[k]))
        elif tag == "replace":
            removed += i2 - i1
            added += j2 - j1
            for k in range(i1, i2):
                entries.append(DiffLine(kind="removed", left_line=left_lines[k], right_line=None))
            for k in range(j1, j2):
                entries.append(DiffLine(kind="added", left_line=None, right_line=right_lines[k]))

    return OutputSummary(
        identical=identical,
        length_a=length_a,
        length_b=length_b,
        length_delta=length_b - length_a,
        added_lines=added,
        removed_lines=removed,
        unchanged_lines=unchanged,
        common_prefix_len=_common_prefix(left, right),
        common_suffix_len=_common_suffix(left, right),
        diff=entries[:MAX_DIFF_HUNKS],
        truncated=truncated,
    )