"""Unit tests for the S3 pytest-summary parser (Phase 5B).

Synthetic pytest output only - no pytest run is executed here beyond these
tests themselves, and no database is touched. Several tests additionally check
the runner's count/exit-code/temp-log contracts source-level - the shell script
itself is never executed here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "pytest_summary.py"
_spec = importlib.util.spec_from_file_location("pytest_summary", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
pytest_summary = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("pytest_summary", pytest_summary)
_spec.loader.exec_module(pytest_summary)

parse = pytest_summary.parse_pytest_summary


def test_baseline_line_with_decorations() -> None:
    summary = parse("======== 682 passed in 41.23s ========")
    assert summary is not None
    assert summary.passed == 682
    assert summary.failed == 0
    assert summary.skipped == 0
    assert summary.errors == 0
    assert summary.xfailed == 0
    assert summary.xpassed == 0
    assert summary.deselected == 0


def test_plain_final_line() -> None:
    summary = parse("682 passed in 1.23s")
    assert summary is not None
    assert summary.passed == 682


def test_db_less_split_line() -> None:
    summary = parse("343 passed, 195 skipped, 144 failed in 12.00s")
    assert summary is not None
    assert summary.passed == 343
    assert summary.skipped == 195
    assert summary.failed == 144


def test_singular_error_word() -> None:
    summary = parse("1 error, 5 passed in 0.10s")
    assert summary is not None
    assert summary.errors == 1
    assert summary.passed == 5


def test_xfail_xpass_and_deselected() -> None:
    summary = parse("3 xfailed, 2 xpassed, 1 skipped in 0.10s")
    assert summary is not None
    assert summary.xfailed == 3
    assert summary.xpassed == 2
    assert summary.skipped == 1
    summary = parse("672 passed, 10 deselected in 1.00s")
    assert summary is not None
    assert summary.deselected == 10
    assert summary.passed == 672


def test_no_tests_ran_is_zero_counts_not_none() -> None:
    summary = parse("no tests ran in 0.01s")
    assert summary is not None
    assert summary.passed == 0
    assert summary.failed == 0


def test_duration_in_minutes_format() -> None:
    summary = parse("======== 682 passed in 1:23.45s ========")
    assert summary is not None
    assert summary.passed == 682


def test_last_duration_line_wins() -> None:
    output = (
        "some nested run: 5 passed in 0.10s\n"
        "FAILED tests/x.py::test_y - assert 3 passed == 4\n"
        "======== 1 failed, 681 passed in 12.00s ========"
    )
    summary = parse(output)
    assert summary is not None
    assert summary.failed == 1
    assert summary.passed == 681


def test_detail_line_without_duration_is_ignored() -> None:
    assert parse("FAILED tests/x.py::test_y - assert 3 passed == 4") is None


def test_interrupt_line_without_duration_is_ignored() -> None:
    assert parse("== Interrupted: 2 errors during collection ==") is None


def test_empty_and_garbage_return_none() -> None:
    assert parse("") is None
    assert parse("garbage output with no counts") is None
    assert parse("connecting to the database...\nretrying...") is None


def test_cli_round_trip(tmp_path: Path, capsys) -> None:
    log = tmp_path / "pytest.log"
    log.write_text("collected 682 items\n\n======== 682 passed in 41.23s ========\n")
    rc = pytest_summary.main(["pytest_summary.py", str(log)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "parsed=1 passed=682 failed=0 skipped=0" in captured.out


def test_cli_unparseable_log_exits_nonzero(tmp_path: Path) -> None:
    log = tmp_path / "broken.log"
    log.write_text("total silence")
    assert pytest_summary.main(["pytest_summary.py", str(log)]) == 1


def test_cli_missing_file_exits_nonzero(tmp_path: Path) -> None:
    assert pytest_summary.main(["pytest_summary.py", str(tmp_path / "no.log")]) == 1


def test_cli_usage_error() -> None:
    assert pytest_summary.main(["pytest_summary.py"]) == 2


def _runner_source() -> str:
    runner_path = Path(__file__).resolve().parents[2] / "scripts" / "test-pytest.sh"
    return runner_path.read_text(encoding="utf-8")


def test_runner_count_gate_expects_745_and_keeps_zero_requirements() -> None:
    """Phase 5B.6.1 (baseline corrected by Phase 5B.13): the expected
    full-suite count is 745 (682 historical + 63 Phase 5B tests: 43 guard +
    20 parser cases, this test included), and the gate still demands zero
    failed, skipped, errors, xfails, xpasses and deselected. Phase 5B.14
    additionally forbids stale baseline literals (682/721/722) anywhere in
    the runner and requires its documentation and success-path message to
    agree with the BASELINE_PASSED value. Source-level assertion - the shell
    gate itself is never executed here.
    """
    source = _runner_source()
    assert "BASELINE_PASSED=745" in source
    assert "BASELINE_FAILED=0" in source
    assert "BASELINE_SKIPPED=0" in source
    for stale_count in ("682", "721", "722"):
        assert stale_count not in source, f"stale expected count {stale_count} survives in the runner"
    for zero_requirement in (
        '[ "${errors:-0}" -ne 0 ]',
        '[ "${xfailed:-0}" -ne 0 ]',
        '[ "${xpassed:-0}" -ne 0 ]',
        '[ "${deselected:-0}" -ne 0 ]',
    ):
        assert zero_requirement in source, f"gate lost a zero requirement: {zero_requirement}"
    baseline_line = next(
        (line for line in source.splitlines() if line.startswith("BASELINE_PASSED=")),
        None,
    )
    assert baseline_line is not None, "runner must define BASELINE_PASSED"
    baseline = baseline_line.split("=", 1)[1]
    assert f"{baseline} passed" in source, "step-9 docs must state the current baseline"
    assert f"{baseline}/0/0" in source, "exit-code docs must state the current baseline"
    assert "count gate: ${BASELINE_PASSED} passed" in source, (
        "success-path message must derive from BASELINE_PASSED, not a literal"
    )


def test_rerun_count_parsed_and_defaults_to_zero() -> None:
    """L9 (Phase 5B.8): the parser surfaces a nonzero rerun count for the
    runner's gate; a summary without reruns parses as rerun=0."""
    summary = parse("722 passed, 3 reruns in 2.00s")
    assert summary is not None
    assert summary.rerun == 3
    singular = parse("======== 721 passed, 1 rerun in 1.00s ========")
    assert singular is not None
    assert singular.rerun == 1
    base = parse("======== 722 passed in 1.00s ========")
    assert base is not None
    assert base.rerun == 0


def test_runner_gates_rerun_to_zero_and_keeps_warnings_report_only() -> None:
    """L9 (Phase 5B.8): any nonzero rerun count fails with the documented
    count-mismatch exit 3, rerun=0 continues to pass with the other zero
    requirements, and warnings are never gated. Source-level."""
    source = _runner_source()
    count_block = source.split("count_gate() {", 1)[1].split("run_dbless() {", 1)[0]
    assert 'rerun="$(summary_field rerun || :)"' in count_block
    assert '[ "${rerun:-0}" -ne 0 ]' in count_block
    assert "0 reruns; got" in count_block, "failure message must report reruns"
    assert "rerun=%s" in count_block, "failure message must print the rerun count"
    assert "summary_field warnings" not in count_block, "warnings must stay report-only"


def test_runner_dbless_success_is_exit_10_and_full_success_stays_0() -> None:
    """L3 (Phase 5B.8): --dbless diagnostic success is distinguishable from a
    full integration pass by exit code (10 vs 0), the contract is documented,
    and the meaningful nonzero failure codes are preserved. Source-level."""
    source = _runner_source()
    dbless_block = source.split("run_dbless() {", 1)[1].split("run_full() {", 1)[0]
    assert "RESULT: PASS (10)" in dbless_block
    assert "exit 10" in dbless_block
    assert "RESULT: PASS (0)" not in dbless_block, "dbless must never print a full-pass code"
    assert "RESULT: FAIL (2)" in dbless_block, "dbless pytest failure code must survive"
    assert 'require_summary "$LOG_FILE"' in dbless_block, "dbless summary failure still exits 3"
    assert "RESULT: FAIL (3)" in source, "count/summary failure code must survive"
    assert "#   10  dbless mode diagnostic success" in source, "exit 10 must be documented"
    full_block = source.split("run_full() {", 1)[1]
    assert "RESULT: PASS (0) - full suite verified" in full_block


def test_runner_removes_temp_log_on_success_and_retains_on_failure() -> None:
    """L11 (Phase 5B.8): both modes create the log via mktemp; the single EXIT
    cleanup removes it only when the run succeeded (exit 0 or dbless 10) and
    otherwise retains it with its path, without ever masking the original exit
    status. Source-level."""
    source = _runner_source()
    assert source.count('LOG_FILE="$(mktemp)"') == 2, "both modes must use LOG_FILE"
    exit_block = source.split("on_exit() {", 1)[1].split("on_signal() {", 1)[0]
    assert '[ -n "$LOG_FILE" ]' in exit_block
    assert '[ "$rc" -eq 0 ] || [ "$rc" -eq 10 ]' in exit_block
    assert 'rm -f "$LOG_FILE"' in exit_block
    assert "retained temporary pytest log for diagnostics" in exit_block
    assert "original exit status preserved" in exit_block
