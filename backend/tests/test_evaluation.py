"""Phase 3E checker + service tests for the prompt evaluation foundation.

The deterministic checker layer is tested as pure functions. ``EvaluationService`` is
exercised against duck-typed dependencies: a fake run resolver for MODE A and a
duck-typed PromptTestingService for MODE B. The AI-free guarantee is asserted both
statically (AST walk over app/evaluation) and in a fresh interpreter. No live provider
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

from app.evaluation import (
    MAX_EXPECTED_OUTPUT_LENGTH,
    MAX_REGEX_LENGTH,
    MAX_RULES,
    EvaluationService,
    EvaluationValidationError,
    EvaluatorConfig,
    Rule,
)
from app.evaluation.checkers import (
    contains,
    exact_match,
    evaluate,
    max_length,
    min_length,
    normalized_match,
    not_contains,
    regex_match,
)
from app.evaluation.types import EvaluationExecution, EvaluationResult
from app.services.errors import NotFoundError

SAMPLE = "Python is a programming language."


def rule(**overrides) -> Rule:
    defaults = {
        "id": "r1",
        "label": "sample rule",
        "type": "contains",
        "text": "Python",
    }
    defaults.update(overrides)
    return Rule(**defaults)


def simple_evaluator(rules: list[Rule]) -> EvaluatorConfig:
    return EvaluatorConfig(rules=rules)


def duck_run(**overrides) -> SimpleNamespace:
    values = {
        "id": uuid.uuid4(),
        "output_text": SAMPLE,
        "provider": "fake",
        "model": "fake-model-1",
        "latency_ms": 12,
        "usage_json": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# ---------------------------------------------------------------------------
# Checkers: per-rule semantics (checklist items 1-18)
# ---------------------------------------------------------------------------


def test_contains_pass():
    passed, evidence = contains(SAMPLE, rule(type="contains", text="Python"))
    assert passed is True
    assert evidence == {"matched": True, "text": "Python"}


def test_contains_fail():
    passed, evidence = contains(SAMPLE, rule(type="contains", text="Java"))
    assert passed is False
    assert evidence == {"matched": False, "text": "Java"}


def test_contains_case_sensitivity():
    insensitive = contains(
        SAMPLE, rule(type="contains", text="python", case_sensitive=False)
    )
    assert insensitive[0] is True
    sensitive = contains(SAMPLE, rule(type="contains", text="python"))
    assert sensitive[0] is False


def test_not_contains_pass():
    passed, evidence = not_contains(SAMPLE, rule(type="not_contains", text="Java"))
    assert passed is True
    assert evidence == {"matched": False, "text": "Java"}


def test_not_contains_fail():
    passed, evidence = not_contains(SAMPLE, rule(type="not_contains", text="Python"))
    assert passed is False
    assert evidence == {"matched": True, "text": "Python"}


def test_min_length_pass():
    passed, evidence = min_length(SAMPLE, rule(type="min_length", length=10))
    assert passed is True
    assert evidence == {"actual_length": len(SAMPLE), "minimum": 10}


def test_min_length_fail():
    passed, _ = min_length(SAMPLE, rule(type="min_length", length=100))
    assert passed is False


def test_max_length_pass():
    passed, evidence = max_length(SAMPLE, rule(type="max_length", length=len(SAMPLE)))
    assert passed is True
    assert evidence == {"actual_length": len(SAMPLE), "maximum": len(SAMPLE)}


def test_max_length_fail():
    passed, _ = max_length(SAMPLE, rule(type="max_length", length=10))
    assert passed is False


def test_exact_match_pass():
    passed, evidence = exact_match(SAMPLE, rule(type="exact_match"), SAMPLE)
    assert passed is True
    assert evidence["matched"] is True
    assert evidence == {
        "matched": True,
        "expected_length": len(SAMPLE),
        "actual_length": len(SAMPLE),
    }


def test_exact_match_fail():
    expected = SAMPLE + "!"
    passed, evidence = exact_match(SAMPLE, rule(type="exact_match"), expected)
    assert passed is False
    assert evidence["expected_length"] == len(expected)
    assert evidence["actual_length"] == len(SAMPLE)


def test_normalized_match_pass():
    messy = "  PYTHON is  a\tprogramming \n language.  "
    passed, evidence = normalized_match(SAMPLE, rule(type="normalized_match"), messy)
    assert passed is True
    assert evidence == {"matched": True}


def test_normalized_match_fail():
    passed, evidence = normalized_match(
        SAMPLE, rule(type="normalized_match"), "Java is a language."
    )
    assert passed is False
    assert evidence == {"matched": False}


def test_regex_match_pass():
    passed, evidence = regex_match(SAMPLE, rule(type="regex_match", pattern=r"\bprogramming\b"))
    assert passed is True
    assert evidence == {"matched": True}


def test_regex_match_fail():
    passed, _ = regex_match(SAMPLE, rule(type="regex_match", pattern=r"\bJava\b"))
    assert passed is False


def test_regex_match_case_insensitive():
    passed, _ = regex_match(
        SAMPLE, rule(type="regex_match", pattern="python", case_sensitive=False)
    )
    assert passed is True


def test_invalid_regex_rejected_at_validation():
    with pytest.raises(ValidationError):
        Rule(type="regex_match", pattern="[")


def test_regex_pattern_bounded():
    with pytest.raises(ValidationError):
        Rule(type="regex_match", pattern="a" * (MAX_REGEX_LENGTH + 1))
    # Exactly at the bound is still valid.
    Rule(type="regex_match", pattern="a" * MAX_REGEX_LENGTH)


def test_rule_limit_bounded():
    one_rule = simple_evaluator([rule()])
    assert len(one_rule.rules) == 1
    many = [rule(id=f"r{i}", text=f"needle {i}") for i in range(MAX_RULES)]
    EvaluatorConfig(rules=many)
    with pytest.raises(ValidationError):
        EvaluatorConfig(rules=many + [rule(id="extra")])
    with pytest.raises(ValidationError):
        EvaluatorConfig(rules=[])


def test_unknown_rule_type_rejected():
    with pytest.raises(ValidationError):
        Rule(type="semantic_judge")  # type: ignore[arg-type]


def test_contains_requires_text():
    with pytest.raises(ValidationError):
        Rule(type="contains", text="   ")
    with pytest.raises(ValidationError):
        Rule(type="contains", text=None)


def test_length_rules_require_length():
    with pytest.raises(ValidationError):
        Rule(type="min_length")
    with pytest.raises(ValidationError):
        Rule(type="max_length")


def test_match_rules_require_expected_output():
    with pytest.raises(ValidationError):
        EvaluatorConfig(rules=[Rule(type="exact_match")])
    with pytest.raises(ValidationError):
        EvaluatorConfig(rules=[Rule(type="normalized_match")])


def test_expected_output_bounded():
    with pytest.raises(ValidationError):
        EvaluatorConfig(
            rules=[Rule(type="exact_match")],
            expected_output="x" * (MAX_EXPECTED_OUTPUT_LENGTH + 1),
        )


def test_evaluate_composer_returns_verdicts():
    verdicts = evaluate(
        SAMPLE,
        [rule(type="contains", text="Python"), rule(type="contains", text="Java")],
        None,
    )
    assert [v.passed for v in verdicts] == [True, False]
    assert [v.type for v in verdicts] == ["contains", "contains"]


# ---------------------------------------------------------------------------
# Service (checklist items 19-25)
# ---------------------------------------------------------------------------


def test_evaluate_existing_run_mode_a():
    run = duck_run()
    captured: dict = {}

    def resolver(db, run_id):
        captured["db"] = db
        captured["run_id"] = run_id
        return run

    service = EvaluationService(object(), run_resolver=resolver)
    session = object()
    result = service.evaluate(
        run_id=run.id, evaluator=simple_evaluator([rule()]), db=session
    )

    assert isinstance(result, EvaluationResult)
    assert result.run_id == run.id
    assert result.output == SAMPLE
    assert result.provider == "fake"
    assert result.model == "fake-model-1"
    assert result.latency_ms == 12
    assert result.usage is not None
    assert result.usage.total_tokens == 12
    assert result.passed is True
    assert captured["db"] is session
    assert captured["run_id"] == run.id


def test_evaluate_ownership_failure_propagates():
    def resolver(db, run_id):
        raise NotFoundError("Prompt run not found")

    service = EvaluationService(object(), run_resolver=resolver)
    with pytest.raises(NotFoundError):
        service.evaluate(run_id=uuid.uuid4(), evaluator=simple_evaluator([rule()]), db=None)


def test_evaluate_fresh_execution_delegates_to_test_service():
    run_id = uuid.uuid4()

    class DuckTestService:
        def __init__(self):
            self.calls = []

        def run(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                output="Simulated answer to: Write a haiku",
                provider="duck",
                model="duck-1",
                finish_reason="stop",
                latency_ms=9,
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4, total_tokens=7),
                request_id=uuid.uuid4(),
                prompt_id=None,
                run_id=run_id,
            )

    duck = DuckTestService()
    service = EvaluationService(duck)
    execution = EvaluationExecution(
        prompt="Write a haiku", test_input="topic: birds", model="duck-1"
    )
    result = service.evaluate(
        evaluator=simple_evaluator([rule(type="contains", text="haiku")]),
        execution=execution,
        db=None,
    )

    assert result.run_id == run_id
    assert result.provider == "duck"
    assert result.model == "duck-1"
    assert result.latency_ms == 9
    assert result.usage is not None and result.usage.total_tokens == 7
    assert result.passed is True
    kwargs = duck.calls[0]
    assert kwargs["prompt"] == "Write a haiku"
    assert kwargs["test_input"] == "topic: birds"
    assert kwargs["model"] == "duck-1"
    assert kwargs["temperature"] == 0.7
    assert kwargs["max_tokens"] == 2048
    assert kwargs["prompt_id"] is None
    assert kwargs["db"] is None


def test_evaluate_fresh_end_to_end_with_duck_gateway():
    """MODE B through the real PromptTestingService + a duck gateway (no AI imports)."""

    class DuckGateway:
        def __init__(self):
            self.calls = []

        def generate(self, db, request):
            self.calls.append((db, request))
            response = SimpleNamespace(
                text="Simulated output for the tested prompt.",
                provider="fake",
                model="fake-model-1",
                finish_reason="stop",
                latency_ms=91,
                usage=SimpleNamespace(prompt_tokens=13, completion_tokens=27, total_tokens=40),
                request_id=uuid.uuid4(),
            )
            return response, None

    from app.testing import PromptTestingService

    gateway = DuckGateway()
    service = EvaluationService(PromptTestingService(gateway))
    result = service.evaluate(
        evaluator=simple_evaluator(
            [
                rule(type="contains", text="tested"),
                rule(type="min_length", length=10),
            ]
        ),
        execution=EvaluationExecution(prompt="Write a haiku"),
        db=None,
    )
    assert result.output == "Simulated output for the tested prompt."
    assert result.provider == "fake"
    assert result.passed is True
    assert len(result.verdicts) == 2


def test_invalid_target_neither_supplied():
    service = EvaluationService(object())
    with pytest.raises(EvaluationValidationError):
        service.evaluate(evaluator=simple_evaluator([rule()]), db=None)


def test_invalid_target_both_supplied():
    service = EvaluationService(object())
    execution = EvaluationExecution(prompt="Write a haiku")
    with pytest.raises(EvaluationValidationError):
        service.evaluate(
            evaluator=simple_evaluator([rule()]),
            run_id=uuid.uuid4(),
            execution=execution,
            db=None,
        )


def test_evaluator_snapshot_preserved_exactly():
    evaluator = EvaluatorConfig(
        name="release gate",
        rules=[rule(id="r9", label="mentions python")],
    )
    service = EvaluationService(
        object(), run_resolver=lambda db, rid: duck_run()
    )
    result = service.evaluate(run_id=uuid.uuid4(), evaluator=evaluator, db=None)

    assert result.evaluator_snapshot == evaluator
    assert result.evaluator_snapshot.model_dump() == evaluator.model_dump()
    assert result.verdicts[0].rule_id == "r9"
    assert result.verdicts[0].label == "mentions python"
    # Defaults are part of the executed snapshot (exactly-as-executed).
    assert result.evaluator_snapshot.rules[0].case_sensitive is True


def test_aggregate_and_behavior():
    runner = lambda db, rid: duck_run()  # noqa: E731
    service = EvaluationService(object(), run_resolver=runner)

    ok = service.evaluate(
        run_id=uuid.uuid4(),
        evaluator=simple_evaluator(
            [rule(type="contains", text="Python"), rule(type="not_contains", text="Java")]
        ),
        db=None,
    )
    assert ok.passed is True

    bad = service.evaluate(
        run_id=uuid.uuid4(),
        evaluator=simple_evaluator(
            [rule(type="contains", text="Python"), rule(type="contains", text="Java")]
        ),
        db=None,
    )
    assert bad.passed is False
    assert [v.passed for v in bad.verdicts] == [True, False]


def test_run_without_output_rejected():
    service = EvaluationService(
        object(), run_resolver=lambda db, rid: duck_run(output_text=None)
    )
    with pytest.raises(EvaluationValidationError):
        service.evaluate(run_id=uuid.uuid4(), evaluator=simple_evaluator([rule()]), db=None)


# ---------------------------------------------------------------------------
# Provider independence (checkers must never touch the AI stack)
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_FORBIDDEN = ("gemini", "nvidia", "ollama", "providers", "gateway", "router", "registry")


def test_evaluation_never_imports_ai_stack_statically():
    """AST walk: no app/evaluation file may import the provider/gateway/router/registry."""
    evaluation_dir = PROJECT_ROOT / "app" / "evaluation"
    files = sorted(evaluation_dir.rglob("*.py"))
    assert files, "app/evaluation must contain Python modules"
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


def test_evaluation_import_and_check_run_in_fresh_interpreter():
    """Importing app.evaluation must not pull app.ai into sys.modules; checkers run clean."""
    script = textwrap.dedent(
        """
        import sys
        import app.evaluation  # noqa: F401
        from app.evaluation.checkers import evaluate
        from app.evaluation.types import EvaluatorConfig, Rule

        rules = [Rule(type="contains", text="Python"), Rule(type="min_length", length=3)]
        verdicts = evaluate("Python is a programming language.", rules, None)
        assert all(v.passed for v in verdicts), verdicts

        leaked = sorted(m for m in sys.modules if m.startswith("app.ai"))
        if leaked:
            print("LEAKED:" + ",".join(leaked))
            sys.exit(1)
        print("CLEAN")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0, (
        f"app.evaluation imported the AI stack or failed to evaluate: {result.stdout}{result.stderr}"
    )
    assert "CLEAN" in result.stdout


def test_checkers_module_has_no_ai_imports_runtime():
    """Runtime probe: nothing bound in app.evaluation.checkers originates from app.ai."""
    import app.evaluation.checkers as checkers

    ai_refs = [
        name
        for name, value in vars(checkers).items()
        if getattr(value, "__module__", "").startswith("app.ai")
    ]
    assert not ai_refs, f"checkers namespace leaks AI objects: {ai_refs}"
    # The only third-party/stdlib dependency is re for regex scanning.
    assert checkers.re is not None