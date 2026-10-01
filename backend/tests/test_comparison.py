"""Phase 3F checker/service tests for the prompt comparison foundation.

Mirrors ``test_evaluation.py`` conventions: pure functions tested directly,
``EvaluationService``/``ComparisonService`` exercised against duck-typed
resolvers (no database), and the AI-free guarantee asserted both statically
(AST walk over app/comparison) and in a fresh interpreter. No live provider
keys, no Ollama, no network.
"""

import ast
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.comparison import (
    MAX_DIFF_CHARS,
    MAX_DIFF_HUNKS,
    MAX_DIFF_LINE_LENGTH,
    MAX_OUTPUT_DISPLAY,
    MAX_SUPPLIED_OUTPUT,
    ComparisonResult,
    ComparisonService,
    ComparisonValidationError,
    EvaluatorCompatibility,
)
from app.comparison.compatibility import compare_evaluators
from app.comparison.criterion import compare_criteria
from app.comparison.diff import line_diff
from app.comparison.metadata import compare_metadata
from app.comparison.service import build_comparison, build_prompt_diff
from app.evaluation import EvaluationService
from app.evaluation.types import (
    CriterionVerdict,
    EvaluationResult,
    EvaluationUsage,
    EvaluatorConfig,
    Rule,
)
from app.services.errors import NotFoundError

SAMPLE = "Python is a programming language."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def rule(**overrides) -> Rule:
    defaults = {
        "id": "r1",
        "label": "sample rule",
        "type": "contains",
        "text": "Python",
    }
    defaults.update(overrides)
    return Rule(**defaults)


def evaluator(rules=None, name="sample evaluator", expected_output=None) -> EvaluatorConfig:
    rules = rules if rules is not None else [rule()]
    payload = {"name": name, "rules": rules}
    if expected_output is not None:
        payload["expected_output"] = expected_output
    return EvaluatorConfig(**payload)


def verdict(
    *,
    rule_id="r1",
    label="sample rule",
    type="contains",
    passed=True,
    evidence=None,
) -> CriterionVerdict:
    return CriterionVerdict(
        rule_id=rule_id,
        label=label,
        type=type,  # type: ignore[arg-type]
        passed=passed,
        evidence=evidence or {"matched": passed, "text": "Python"},
    )


def make_evaluation(
    *,
    output=SAMPLE,
    rules=None,
    verdicts=None,
    provider="fake",
    model="fake-model-1",
    latency_ms=None,
    usage=None,
    run_id=None,
) -> EvaluationResult:
    rules = rules if rules is not None else [rule()]
    verdicts = verdicts if verdicts is not None else [verdict()]
    return EvaluationResult(
        run_id=run_id,
        output=output,
        provider=provider,
        model=model,
        latency_ms=latency_ms,
        usage=usage,
        evaluator_snapshot=evaluator(rules),
        verdicts=verdicts,
        passed=all(v.passed for v in verdicts),
    )


def duck_run(**overrides) -> SimpleNamespace:
    values = {
        "id": uuid.uuid4(),
        "output_text": SAMPLE,
        "provider": "fake",
        "model": "fake-model-1",
        "latency_ms": 12,
        "usage_json": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        "input_snapshot": {"messages": [{"role": "user", "content": "Write a welcome message"}]},
        "finish_reason": "stop",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def resolver_for(runs):
    by_id = {run.id: run for run in runs}

    def resolver(db, run_id):
        try:
            return by_id[run_id]
        except KeyError:
            raise NotFoundError("Prompt run not found")

    return resolver


def rule_service(runs) -> ComparisonService:
    """A ComparisonService whose EvaluationService shares the same run resolver."""
    resolver = resolver_for(runs)
    eval_service = EvaluationService(object(), run_resolver=resolver)
    return ComparisonService(eval_service, run_resolver=resolver)


# ---------------------------------------------------------------------------
# Compatibility (checklist items 1-12)
# ---------------------------------------------------------------------------


def test_identical_snapshots_comparable():
    compat = compare_evaluators(evaluator([rule()]), evaluator([rule()]))
    assert compat.comparable is True
    assert compat.status == "identical"
    assert compat.mismatches == []


def test_different_text_incompatible():
    left = evaluator([rule(text="Python")])
    right = evaluator([rule(text="Java")])
    compat = compare_evaluators(left, right)
    assert compat.comparable is False
    assert compat.status == "incompatible"
    assert len(compat.mismatches) == 1
    mismatch = compat.mismatches[0]
    assert mismatch.rule_index == 0
    assert mismatch.field == "text"
    assert mismatch.left_value == "Python"
    assert mismatch.right_value == "Java"


def test_different_length_incompatible():
    compat = compare_evaluators(
        evaluator([rule(type="min_length", length=100)]),
        evaluator([rule(type="min_length", length=200)]),
    )
    assert compat.comparable is False
    assert compat.mismatches[0].field == "length"
    assert compat.mismatches[0].left_value == 100
    assert compat.mismatches[0].right_value == 200


def test_different_pattern_incompatible():
    compat = compare_evaluators(
        evaluator([rule(type="regex_match", pattern=r"\bfoo\b")]),
        evaluator([rule(type="regex_match", pattern=r"\bbar\b")]),
    )
    assert compat.comparable is False
    assert compat.mismatches[0].field == "pattern"


def test_different_type_incompatible():
    compat = compare_evaluators(
        evaluator([rule(type="contains", text="Python")]),
        evaluator([rule(type="min_length", length=10)]),
    )
    assert compat.comparable is False
    assert compat.mismatches[0].field == "type"


def test_different_case_sensitive_incompatible():
    compat = compare_evaluators(
        evaluator([rule(type="contains", text="Python", case_sensitive=True)]),
        evaluator([rule(type="contains", text="Python", case_sensitive=False)]),
    )
    assert compat.comparable is False
    assert compat.mismatches[0].field == "case_sensitive"


def test_different_strip_incompatible():
    compat = compare_evaluators(
        evaluator([rule(type="min_length", length=10, strip=True)]),
        evaluator([rule(type="min_length", length=10, strip=False)]),
    )
    assert compat.comparable is False
    assert compat.mismatches[0].field == "strip"


def test_different_expected_output_incompatible():
    exact = lambda out: evaluator(  # noqa: E731
        [rule(type="exact_match")], expected_output=out
    )
    compat = compare_evaluators(exact("answer one"), exact("answer two"))
    assert compat.comparable is False
    mismatch = compat.mismatches[0]
    assert mismatch.rule_index == -1
    assert mismatch.field == "expected_output"
    assert mismatch.left_value == "answer one"
    assert mismatch.right_value == "answer two"


def test_different_name_remains_compatible():
    compat = compare_evaluators(
        evaluator([rule()], name="left evaluator"),
        evaluator([rule()], name="right evaluator"),
    )
    assert compat.comparable is True
    assert compat.mismatches == []


def test_different_rule_id_remains_compatible():
    compat = compare_evaluators(
        evaluator([rule(id="left-id")]),
        evaluator([rule(id="right-id")]),
    )
    assert compat.comparable is True
    assert compat.mismatches == []


def test_different_label_remains_compatible():
    compat = compare_evaluators(
        evaluator([rule(label="left label")]),
        evaluator([rule(label="right label")]),
    )
    assert compat.comparable is True
    assert compat.mismatches == []


def test_positional_alignment():
    left = evaluator(
        [rule(id="a", type="contains", text="Python"), rule(id="b", type="min_length", length=50)]
    )
    right = evaluator(
        [rule(id="b", type="min_length", length=50), rule(id="a", type="contains", text="Python")]
    )
    compat = compare_evaluators(left, right)
    assert compat.comparable is False
    positions = sorted(m.rule_index for m in compat.mismatches)
    assert 0 in positions
    # The mismatch at position 0 is a type change (contains vs min_length).
    first = next(m for m in compat.mismatches if m.rule_index == 0 and m.field == "type")
    assert first.left_value == "contains"
    assert first.right_value == "min_length"


def test_rule_count_mismatch_incompatible():
    compat = compare_evaluators(
        evaluator([rule(), rule(text="Java")]),
        evaluator([rule()]),
    )
    assert compat.comparable is False
    assert compat.mismatches[0].field == "rule_count"
    assert compat.mismatches[0].rule_index == -1


# ---------------------------------------------------------------------------
# Criterion diff (checklist items 13-17)
# ---------------------------------------------------------------------------


def _two_results(left_verdict, right_verdict) -> tuple[EvaluationResult, EvaluationResult]:
    rules = [rule()]
    left = make_evaluation(rules=rules, verdicts=[left_verdict])
    right = make_evaluation(rules=rules, verdicts=[right_verdict])
    return left, right


def test_criterion_same_pass():
    left, right = _two_results(verdict(passed=True), verdict(passed=True))
    rows = compare_criteria(left, right)
    assert len(rows) == 1
    assert rows[0].state == "same_pass"
    assert rows[0].left.passed is True
    assert rows[0].right.passed is True
    assert rows[0].rule_index == 0


def test_criterion_same_fail():
    left, right = _two_results(verdict(passed=False), verdict(passed=False))
    rows = compare_criteria(left, right)
    assert rows[0].state == "same_fail"


def test_criterion_left_only_pass():
    left, right = _two_results(verdict(passed=True), verdict(passed=False))
    rows = compare_criteria(left, right)
    assert rows[0].state == "left_only_pass"


def test_criterion_right_only_pass():
    left, right = _two_results(verdict(passed=False), verdict(passed=True))
    rows = compare_criteria(left, right)
    assert rows[0].state == "right_only_pass"


def test_criterion_evidence_delta():
    left_verdict = verdict(
        type="min_length",
        passed=False,
        evidence={"actual_length": 44, "minimum": 50},
    )
    right_verdict = verdict(
        type="min_length",
        passed=False,
        evidence={"actual_length": 61, "minimum": 50},
    )
    rows = compare_criteria(
        make_evaluation(verdicts=[left_verdict]),
        make_evaluation(verdicts=[right_verdict]),
    )
    assert rows[0].evidence_delta == {"actual_length": {"left": 44, "right": 61}}
    # Identical evidence produces no delta.
    same = compare_criteria(
        make_evaluation(verdicts=[verdict(passed=False)]),
        make_evaluation(verdicts=[verdict(passed=False)]),
    )
    assert same[0].evidence_delta is None


# ---------------------------------------------------------------------------
# Output diff (checklist items 18-24)
# ---------------------------------------------------------------------------


def test_output_identical():
    summary = line_diff("line1\nline2", "line1\nline2")
    assert summary.identical is True
    assert summary.added_lines == 0
    assert summary.removed_lines == 0
    assert summary.unchanged_lines == 2
    assert summary.length_delta == 0
    assert summary.truncated is False
    assert summary.common_prefix_len == 11


def test_output_different():
    summary = line_diff("line1\nline2", "line1\nline3")
    assert summary.identical is False
    assert summary.added_lines == 1
    assert summary.removed_lines == 1
    assert summary.unchanged_lines == 1
    kinds = [entry.kind for entry in summary.diff]
    assert "context" in kinds
    assert "added" in kinds
    assert "removed" in kinds
    removed = next(e for e in summary.diff if e.kind == "removed")
    added = next(e for e in summary.diff if e.kind == "added")
    assert removed.left_line == "line2"
    assert removed.right_line is None
    assert added.left_line is None
    assert added.right_line == "line3"


def test_output_both_empty():
    summary = line_diff("", "")
    assert summary.identical is True
    assert summary.added_lines == 0
    assert summary.removed_lines == 0
    assert summary.unchanged_lines == 0
    assert summary.diff == []
    assert summary.common_prefix_len == 0
    assert summary.common_suffix_len == 0


def test_output_one_empty():
    summary = line_diff("", "abc")
    assert summary.identical is False
    assert summary.added_lines == 1
    assert summary.removed_lines == 0
    assert summary.length_delta == 3


def test_output_truncation():
    summary = line_diff("a" * 25_000, "b" * 25_000)
    assert summary.truncated is True
    # Factual original lengths are preserved even when diff inputs are capped.
    assert summary.length_a == 25_000
    assert summary.length_b == 25_000
    assert summary.length_delta == 0
    assert summary.added_lines == 1
    assert summary.removed_lines == 1
    for entry in summary.diff:
        for line in (entry.left_line, entry.right_line):
            assert line is None or len(line) <= MAX_DIFF_LINE_LENGTH


def test_output_unicode():
    summary = line_diff("héllo ✓", "héllo ✗")
    assert summary.identical is False
    kinds = [entry.kind for entry in summary.diff]
    assert "removed" in kinds
    assert "added" in kinds


def test_output_whitespace_changes():
    summary = line_diff("hello world", "hello  world")
    assert summary.identical is False
    assert summary.removed_lines == 1
    assert summary.added_lines == 1


def test_output_diff_hunks_bounded():
    left = "\n".join(f"keep {i}" for i in range(1_000))
    right = "\n".join(f"changed {i}" for i in range(1_000))
    summary = line_diff(left, right)
    assert len(summary.diff) <= MAX_DIFF_HUNKS


# ---------------------------------------------------------------------------
# Metadata diff (checklist items 25-30)
# ---------------------------------------------------------------------------


def test_metadata_same():
    usage = EvaluationUsage(prompt_tokens=5, completion_tokens=7, total_tokens=12)
    diff = compare_metadata(
        make_evaluation(latency_ms=12, usage=usage),
        make_evaluation(latency_ms=12, usage=usage),
        left_finish_reason="stop",
        right_finish_reason="stop",
    )
    assert diff.provider.changed is False
    assert diff.model.changed is False
    assert diff.finish_reason.changed is False
    assert diff.latency_ms.changed is False
    assert diff.latency_delta == 0
    assert diff.usage.prompt_tokens.changed is False
    assert diff.usage.prompt_tokens.left == 5
    assert diff.usage.prompt_tokens.right == 5
    assert diff.usage.total_tokens.left == 12
    assert diff.usage.total_tokens.right == 12


def test_provider_changed():
    diff = compare_metadata(make_evaluation(provider="fake"), make_evaluation(provider="other"))
    assert diff.provider.changed is True
    assert diff.provider.left == "fake"
    assert diff.provider.right == "other"


def test_model_changed():
    diff = compare_metadata(
        make_evaluation(model="fake-model-1"), make_evaluation(model="fake-model-2")
    )
    assert diff.model.changed is True


def test_latency_delta():
    diff = compare_metadata(
        make_evaluation(latency_ms=842), make_evaluation(latency_ms=1_230)
    )
    assert diff.latency_delta == 388
    assert diff.latency_ms.changed is True
    assert diff.latency_ms.left == 842
    assert diff.latency_ms.right == 1_230


def test_latency_delta_absent_when_missing():
    diff = compare_metadata(
        make_evaluation(latency_ms=None), make_evaluation(latency_ms=1_230)
    )
    assert diff.latency_delta is None
    assert diff.latency_ms.changed is True


def test_missing_usage():
    diff = compare_metadata(
        make_evaluation(
            usage=EvaluationUsage(prompt_tokens=5, completion_tokens=7, total_tokens=12)
        ),
        make_evaluation(usage=None),
    )
    assert diff.usage is not None
    assert diff.usage.prompt_tokens.left == 5
    assert diff.usage.prompt_tokens.right is None
    assert diff.usage.prompt_tokens.changed is True
    # Neither side reported usage -> usage is None (never fabricated).
    both_none = compare_metadata(make_evaluation(usage=None), make_evaluation(usage=None))
    assert both_none.usage is None


def test_finish_reason_changed():
    diff = compare_metadata(
        make_evaluation(),
        make_evaluation(),
        left_finish_reason="stop",
        right_finish_reason="length",
    )
    assert diff.finish_reason.changed is True
    assert diff.finish_reason.left == "stop"
    assert diff.finish_reason.right == "length"


# ---------------------------------------------------------------------------
# Prompt diff (checklist items 31-33)
# ---------------------------------------------------------------------------


def test_prompt_identical():
    snapshot = {"messages": [{"role": "user", "content": "Write a welcome message"}]}
    left = duck_run(input_snapshot=snapshot)
    right = duck_run(input_snapshot=snapshot)
    prompt = build_prompt_diff(left, right)
    assert prompt is not None
    assert prompt.present is True
    assert prompt.identical is True
    assert prompt.left_length == prompt.right_length
    assert prompt.length_delta == 0


def test_prompt_different():
    left = duck_run(
        input_snapshot={"messages": [{"role": "user", "content": "Write a welcome message"}]}
    )
    right = duck_run(
        input_snapshot={"messages": [{"role": "user", "content": "Write a farewell message"}]}
    )
    prompt = build_prompt_diff(left, right)
    assert prompt is not None
    assert prompt.identical is False
    assert prompt.left_length != prompt.right_length
    assert prompt.length_delta == prompt.right_length - prompt.left_length
    assert any(e.kind != "context" for e in prompt.diff)


def test_prompt_missing_snapshot():
    left = duck_run(input_snapshot=None)
    right = duck_run(input_snapshot={"messages": [{"role": "user", "content": "hi"}]})
    assert build_prompt_diff(left, right) is None
    assert build_prompt_diff(right, left) is None
    malformed = duck_run(input_snapshot={"not": "a snapshot"})
    assert build_prompt_diff(left, malformed) is None


# ---------------------------------------------------------------------------
# Service (checklist items 34-42)
# ---------------------------------------------------------------------------


def test_run_pair_success():
    left = duck_run(output_text="needle found")
    right = duck_run(output_text="needle found too")
    service = rule_service([left, right])
    result = service.compare(
        left_run_id=left.id,
        right_run_id=right.id,
        evaluator=evaluator([rule(text="needle")]),
    )
    assert isinstance(result, ComparisonResult)
    assert result.left_execution.run_id == left.id
    assert result.right_execution.run_id == right.id
    assert result.evaluator_compatibility.comparable is True
    assert len(result.criterion_diffs) == 1
    assert result.prompt_diff is not None
    assert result.metadata_diff.provider.changed is False
    assert result.output_summary.length_a == 12
    assert result.left_execution.output.length == len("needle found")


def test_run_pair_ownership_checked_before_comparison():
    owned = duck_run(output_text="needle owned")
    foreign = SimpleNamespace(id=uuid.uuid4())
    service = rule_service([owned])
    with pytest.raises(NotFoundError):
        service.compare(
            left_run_id=owned.id,
            right_run_id=foreign.id,
            evaluator=evaluator([rule(text="needle")]),
        )
    with pytest.raises(NotFoundError):
        service.compare(
            left_run_id=foreign.id,
            right_run_id=owned.id,
            evaluator=evaluator([rule(text="needle")]),
        )
    with pytest.raises(NotFoundError):
        service.compare(
            left_run_id=uuid.uuid4(),
            right_run_id=uuid.uuid4(),
            evaluator=evaluator([rule(text="needle")]),
        )


def test_run_pair_uses_same_evaluator_on_both_sides():
    left = duck_run(output_text="needle left")
    right = duck_run(output_text="needle right")

    def eval_resolver(db, run_id):
        return left if run_id == left.id else right

    eval_service = EvaluationService(object(), run_resolver=eval_resolver)
    comparison = ComparisonService(eval_service, run_resolver=eval_resolver)
    result = comparison.compare(
        left_run_id=left.id,
        right_run_id=right.id,
        evaluator=evaluator([rule(text="needle"), rule(type="min_length", length=1)]),
    )
    assert len(result.criterion_diffs) == 2
    assert result.evaluator_compatibility.comparable is True


def test_supplied_mode_no_db():
    left = make_evaluation(output="alpha")
    right = make_evaluation(output="alpha beta")
    called = []

    def never(db, run_id):  # pragma: no cover
        called.append(run_id)
        raise AssertionError("supplied mode must not resolve runs")

    service = ComparisonService(EvaluationService(object(), run_resolver=never), run_resolver=never)
    result = service.compare(left_evaluation=left, right_evaluation=right)
    assert result.left_execution.run_id is None
    assert result.evaluator_compatibility.comparable is True
    assert result.prompt_diff is None  # no run snapshots in supplied mode
    assert result.output_summary.length_a == 5
    assert result.output_summary.length_b == 10  # "alpha beta"
    assert called == []


def test_incompatible_supplied_evaluators():
    left = make_evaluation(
        output="alpha long enough",
        rules=[rule(text="alpha")],
        verdicts=[verdict(passed=True)],
    )
    right = make_evaluation(
        output="beta long enough",
        rules=[rule(text="beta")],
        verdicts=[verdict(passed=False)],
    )
    result = build_comparison(left, right)
    assert result.evaluator_compatibility.comparable is False
    assert result.evaluator_compatibility.status == "incompatible"
    assert result.criterion_diffs == []
    # Output comparison is still reported.
    assert result.output_summary.identical is False


def test_both_modes_supplied_rejected():
    service = rule_service([])
    with pytest.raises(ComparisonValidationError):
        service.compare(
            left_run_id=uuid.uuid4(),
            right_run_id=uuid.uuid4(),
            evaluator=evaluator([rule()]),
            left_evaluation=make_evaluation(),
            right_evaluation=make_evaluation(),
        )


def test_neither_mode_supplied_rejected():
    service = rule_service([])
    with pytest.raises(ComparisonValidationError):
        service.compare()


def test_partial_run_pair_rejected():
    service = rule_service([])
    with pytest.raises(ComparisonValidationError):
        service.compare(left_run_id=uuid.uuid4(), right_run_id=uuid.uuid4())
    with pytest.raises(ComparisonValidationError):
        service.compare(left_run_id=uuid.uuid4(), right_run_id=uuid.uuid4(), evaluator=None)


def test_run_pair_is_stateless():
    left = duck_run(output_text="needle stateless")
    right = duck_run(output_text="needle stateless too")
    before_left = {field: getattr(left, field) for field in vars(left)}
    before_right = {field: getattr(right, field) for field in vars(right)}
    service = rule_service([left, right])
    service.compare(
        left_run_id=left.id,
        right_run_id=right.id,
        evaluator=evaluator([rule(text="needle")]),
    )
    after_left = {field: getattr(left, field) for field in vars(left)}
    after_right = {field: getattr(right, field) for field in vars(right)}
    assert after_left == before_left
    assert after_right == before_right


def test_execution_output_is_bounded():
    big = "x" * (MAX_OUTPUT_DISPLAY * 2)
    result = build_comparison(
        make_evaluation(output=big), make_evaluation(output="small")
    )
    assert result.left_execution.output.truncated is True
    assert result.left_execution.output.length == len(big)
    assert len(result.left_execution.output.text) == MAX_OUTPUT_DISPLAY


def test_supplied_output_cap_constant():
    assert MAX_SUPPLIED_OUTPUT > MAX_OUTPUT_DISPLAY


# ---------------------------------------------------------------------------
# Provider independence (app.comparison must never touch the AI stack)
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_FORBIDDEN = ("gemini", "nvidia", "ollama", "providers", "gateway", "router", "registry")


def test_comparison_never_imports_ai_stack_statically():
    """AST walk: no app/comparison file may import the provider/gateway/router/registry."""
    comparison_dir = PROJECT_ROOT / "app" / "comparison"
    files = sorted(comparison_dir.rglob("*.py"))
    assert files, "app/comparison must contain Python modules"
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(
                        part in alias.name.lower() for part in _FORBIDDEN
                    ), f"{path}: forbidden import {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not any(
                    part in node.module.lower() for part in _FORBIDDEN
                ), f"{path}: forbidden import {node.module}"


def test_comparison_import_and_compare_in_fresh_interpreter():
    """Importing app.comparison must not pull app.ai into sys.modules; pure
    comparison work runs clean end-to-end in a fresh interpreter."""
    script = textwrap.dedent(
        """
        import sys
        import app.comparison  # noqa: F401
        from app.comparison import build_comparison, line_diff
        from app.evaluation.types import EvaluatorConfig, Rule, CriterionVerdict
        from app.evaluation import EvaluationResult

        def make_evaluation(output, passed):
            rule = Rule(type="contains", text="needle")
            return EvaluationResult(
                output=output,
                provider="fake",
                model="m1",
                evaluator_snapshot=EvaluatorConfig(rules=[rule]),
                verdicts=[
                    CriterionVerdict(rule_id="r1", label="rule", type="contains", passed=passed, evidence={"matched": passed})
                ],
                passed=passed,
            )

        summary = line_diff("a\\nb", "a\\nc")
        assert not summary.identical and summary.added_lines == 1 and summary.removed_lines == 1

        result = build_comparison(
            make_evaluation("needle here", True),
            make_evaluation("needle elsewhere", False),
        )
        assert result.evaluator_compatibility.comparable is True
        assert result.criterion_diffs[0].state == "left_only_pass"
        assert result.metadata_diff.provider.changed is False

        leaked = sorted(m for m in sys.modules if m.startswith("app.ai"))
        if leaked:
            print("LEAKED:" + ",".join(leaked))
            sys.exit(1)
        print("CLEAN")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"fresh interpreter failed:\n{result.stdout}\n{result.stderr}"