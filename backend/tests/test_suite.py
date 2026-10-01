"""Phase 3G suite tests: domain contracts + ``EvaluationSuiteService``.

Mirrors ``test_evaluation.py`` / ``test_comparison.py`` rigor: duck-typed runs and
resolvers for MODE A, duck-typed PromptTestingService for MODE B, ownership-first
semantics, and an AI-free import surface asserted in a fresh interpreter (the AST walk
in ``test_evaluation.py`` already auto-covers every ``app/evaluation`` file). No live
provider keys, no Ollama, no network.
"""

import json
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.evaluation import (
    MAX_SUITE_TARGETS,
    EvaluationService,
    EvaluationSuiteResult,
    EvaluationSuiteService,
    EvaluationSuiteTarget,
    EvaluationValidationError,
    EvaluatorConfig,
    Rule,
)
from app.evaluation.types import EvaluationExecution, EvaluationResult
from app.services.errors import NotFoundError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE = "Simulated answer to: Write a welcome message"


def rule(**overrides) -> Rule:
    defaults = {
        "id": "r1",
        "label": "sample rule",
        "type": "contains",
        "text": "Simulated answer",
    }
    defaults.update(overrides)
    return Rule(**defaults)


def simple_evaluator(rules: list[Rule], name="sample evaluator") -> EvaluatorConfig:
    return EvaluatorConfig(name=name, rules=rules)


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


def resolver_for(runs):
    """Owned-run resolver that serves ONLY the supplied runs (NotFoundError else)."""
    by_id = {run.id: run for run in runs}

    def resolver(db, run_id):
        if run_id in by_id:
            return by_id[run_id]
        raise NotFoundError("Prompt run not found")

    return resolver


def suite_service_for(runs, test_service=None) -> EvaluationSuiteService:
    """Suite service sharing ONE resolver between its own MODE A pass and the eval service."""
    run_resolver = resolver_for(runs)
    eval_service = EvaluationService(
        test_service if test_service is not None else object(),
        run_resolver=run_resolver,
    )
    return EvaluationSuiteService(eval_service, run_resolver=run_resolver)


def execution_for(prompt="Write a welcome message") -> EvaluationExecution:
    return EvaluationExecution(prompt=prompt)


# ---------------------------------------------------------------------------
# Domain contracts
# ---------------------------------------------------------------------------


def test_suite_target_xor_both_rejected():
    with pytest.raises(ValidationError):
        EvaluationSuiteTarget(
            run_id=uuid.uuid4(), execution=execution_for()
        )


def test_suite_target_xor_neither_rejected():
    with pytest.raises(ValidationError):
        EvaluationSuiteTarget()


def test_suite_target_run_id_only():
    target = EvaluationSuiteTarget(run_id=uuid.uuid4())
    assert target.execution is None


def test_suite_target_execution_only():
    target = EvaluationSuiteTarget(execution=execution_for())
    assert target.run_id is None


def test_suite_result_counts_are_integers_only():
    result = EvaluationSuiteResult(
        total=3,
        passed=1,
        evaluations=[
            EvaluationResult(
                run_id=None,
                output=SAMPLE,
                provider="fake",
                model="fake-model-1",
                latency_ms=12,
                usage=None,
                evaluator_snapshot=simple_evaluator([rule()]),
                verdicts=[],
                passed=False,
            )
            for _ in range(3)
        ],
    )
    assert result.total == 3
    assert result.passed == 1
    assert isinstance(result.total, int)
    assert isinstance(result.passed, int)
    assert len(result.evaluations) == 3
    assert result.generated_at is not None


def test_max_suite_targets_ceiling():
    assert MAX_SUITE_TARGETS == 20


# ---------------------------------------------------------------------------
# MODE A: all-run suite
# ---------------------------------------------------------------------------


def test_suite_three_runs_all_pass_counts():
    runs = [duck_run() for _ in range(3)]
    service = suite_service_for(runs)
    result = service.run_suite(
        evaluator=simple_evaluator([rule()]),
        targets=[EvaluationSuiteTarget(run_id=run.id) for run in runs],
        db=None,
    )
    assert isinstance(result, EvaluationSuiteResult)
    assert result.total == 3
    assert result.passed == 3
    assert [e.run_id for e in result.evaluations] == [run.id for run in runs]


def test_suite_runs_mixed_counts():
    runs = [duck_run(), duck_run(output_text="nothing expected here"), duck_run()]
    service = suite_service_for(runs)
    result = service.run_suite(
        evaluator=simple_evaluator([rule()]),
        targets=[EvaluationSuiteTarget(run_id=run.id) for run in runs],
        db=None,
    )
    assert result.total == 3
    assert result.passed == 2
    assert [e.passed for e in result.evaluations] == [True, False, True]


def test_suite_runs_all_fail_counts():
    runs = [duck_run(output_text="miss"), duck_run(output_text="also miss")]
    service = suite_service_for(runs)
    result = service.run_suite(
        evaluator=simple_evaluator([rule()]),
        targets=[EvaluationSuiteTarget(run_id=run.id) for run in runs],
        db=None,
    )
    assert result.total == 2
    assert result.passed == 0


def test_suite_single_run_parity_with_evaluation_service():
    """One run target through the suite == direct EvaluationService.evaluate."""
    run = duck_run()
    evaluator = simple_evaluator([rule()])
    direct = EvaluationService(
        object(), run_resolver=lambda db, rid: run
    ).evaluate(evaluator=evaluator, run_id=run.id, db=None)

    service = suite_service_for([run])
    result = service.run_suite(
        evaluator=evaluator,
        targets=[EvaluationSuiteTarget(run_id=run.id)],
        db=None,
    )
    assert result.total == 1
    assert result.passed == 1
    # Identical up to the generated_at timestamp (defaults to now on each build).
    assert result.evaluations[0].model_dump(
        exclude={"generated_at"}
    ) == direct.model_dump(exclude={"generated_at"})


def test_suite_unknown_run_not_found():
    service = suite_service_for([])
    with pytest.raises(NotFoundError):
        service.run_suite(
            evaluator=simple_evaluator([rule()]),
            targets=[EvaluationSuiteTarget(run_id=uuid.uuid4())],
            db=None,
        )


# ---------------------------------------------------------------------------
# MODE B: fresh-execution suite
# ---------------------------------------------------------------------------


class DuckTestService:
    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            output=SAMPLE,
            provider="duck",
            model="duck-1",
            finish_reason="stop",
            latency_ms=9,
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4, total_tokens=7),
            request_id=uuid.uuid4(),
            prompt_id=None,
            run_id=None,
        )


def test_suite_fresh_executions_preserve_order_and_counts():
    duck = DuckTestService()
    service = suite_service_for([], test_service=duck)
    targets = [
        EvaluationSuiteTarget(execution=execution_for("Write a welcome message")),
        EvaluationSuiteTarget(execution=execution_for("Write a thank-you note")),
        EvaluationSuiteTarget(execution=execution_for("Write a farewell")),
    ]
    result = service.run_suite(
        evaluator=simple_evaluator([rule()]),
        targets=targets,
        db=None,
    )
    assert result.total == 3
    assert result.passed == 3
    assert all(e.run_id is None for e in result.evaluations)
    # Same evaluator and same execution order reached the test service.
    assert [call["prompt"] for call in duck.calls] == [
        "Write a welcome message",
        "Write a thank-you note",
        "Write a farewell",
    ]
    assert all(call["db"] is None for call in duck.calls)


def test_suite_fresh_executions_carry_evaluator_through():
    duck = DuckTestService()
    service = suite_service_for([], test_service=duck)
    # Two rules, both must pass for the run to pass: contains + min_length on SAMPLE.
    evaluator = simple_evaluator(
        [rule(), rule(id="r2", label="short", type="min_length", length=3)]
    )
    result = service.run_suite(
        evaluator=evaluator,
        targets=[EvaluationSuiteTarget(execution=execution_for())],
        db=None,
    )
    assert result.total == 1
    assert result.passed == 1  # both rules were applied to the duck output
    assert len(duck.calls) == 1


# ---------------------------------------------------------------------------
# Mixed suite (MODE A + MODE B) preserves order
# ---------------------------------------------------------------------------


def test_suite_mixed_run_and_execution_preserves_order():
    runs = [duck_run() for _ in range(2)]
    duck = DuckTestService()
    service = suite_service_for(runs, test_service=duck)
    targets = [
        EvaluationSuiteTarget(run_id=runs[0].id),
        EvaluationSuiteTarget(execution=execution_for()),
        EvaluationSuiteTarget(run_id=runs[1].id),
    ]
    result = service.run_suite(
        evaluator=simple_evaluator([rule()]),
        targets=targets,
        db=None,
    )
    assert result.total == 3
    assert result.passed == 3
    assert result.evaluations[0].run_id == runs[0].id
    assert result.evaluations[1].run_id is None
    assert result.evaluations[2].run_id == runs[1].id


# ---------------------------------------------------------------------------
# Ownership-first MODE A semantics
# ---------------------------------------------------------------------------


def test_suite_ownership_first_resolves_all_runs_before_evaluating():
    """A foreign run later in the list must fail the suite with zero evaluation."""

    runs = [duck_run(), duck_run()]
    foreign_id = uuid.uuid4()
    resolver = resolver_for(runs)
    evaluated: list = []

    class RecordingEval(EvaluationService):
        def evaluate(self, **kwargs):
            evaluated.append(kwargs)
            return super().evaluate(**kwargs)

    service = EvaluationSuiteService(
        RecordingEval(object(), run_resolver=resolver), run_resolver=resolver
    )
    with pytest.raises(NotFoundError):
        service.run_suite(
            evaluator=simple_evaluator([rule()]),
            targets=[
                EvaluationSuiteTarget(run_id=runs[0].id),
                EvaluationSuiteTarget(run_id=runs[1].id),
                EvaluationSuiteTarget(run_id=foreign_id),
            ],
            db=None,
        )
    # Neither owned run was evaluated: resolution of ALL run ids happens first.
    assert evaluated == []


def test_suite_ownership_first_no_duplicate_resolution():
    runs = [duck_run()]
    resolver = resolver_for(runs)
    resolved: list = []

    def counting_resolver(db, run_id):
        resolved.append(run_id)
        return resolver(db, run_id)

    service = EvaluationSuiteService(
        EvaluationService(object(), run_resolver=counting_resolver),
        run_resolver=counting_resolver,
    )
    result = service.run_suite(
        evaluator=simple_evaluator([rule()]),
        targets=[EvaluationSuiteTarget(run_id=runs[0].id)],
        db=None,
    )
    assert result.passed == 1
    # One ownership check + one evaluate-time resolve of the same run.
    assert len(resolved) == 2
    assert all(rid == runs[0].id for rid in resolved)


# ---------------------------------------------------------------------------
# Defensive validation (service layer, mirrors the wire contracts)
# ---------------------------------------------------------------------------


def test_suite_empty_targets_rejected():
    service = suite_service_for([])
    with pytest.raises(EvaluationValidationError):
        service.run_suite(
            evaluator=simple_evaluator([rule()]), targets=[], db=None
        )


def test_suite_too_many_targets_rejected():
    service = suite_service_for([])
    targets = [EvaluationSuiteTarget(execution=execution_for(str(i))) for i in range(21)]
    with pytest.raises(EvaluationValidationError):
        service.run_suite(
            evaluator=simple_evaluator([rule()]), targets=targets, db=None
        )


def test_suite_exactly_20_targets_accepted():
    duck = DuckTestService()
    service = suite_service_for([], test_service=duck)
    targets = [EvaluationSuiteTarget(execution=execution_for(str(i))) for i in range(20)]
    result = service.run_suite(
        evaluator=simple_evaluator([rule()]), targets=targets, db=None
    )
    assert result.total == 20
    assert result.passed == 20


def test_suite_duplicate_run_ids_rejected():
    run = duck_run()
    service = suite_service_for([run])
    with pytest.raises(EvaluationValidationError):
        service.run_suite(
            evaluator=simple_evaluator([rule()]),
            targets=[
                EvaluationSuiteTarget(run_id=run.id),
                EvaluationSuiteTarget(run_id=run.id),
            ],
            db=None,
        )


def test_suite_duplicate_executions_rejected():
    service = suite_service_for([])
    execution = execution_for()
    with pytest.raises(EvaluationValidationError):
        service.run_suite(
            evaluator=simple_evaluator([rule()]),
            targets=[
                EvaluationSuiteTarget(execution=execution),
                EvaluationSuiteTarget(execution=execution),
            ],
            db=None,
        )


def test_suite_duplicate_executions_canonical_json():
    """Semantically identical executions (explicit defaults) are still duplicates."""
    service = suite_service_for([])
    with pytest.raises(EvaluationValidationError):
        service.run_suite(
            evaluator=simple_evaluator([rule()]),
            targets=[
                EvaluationSuiteTarget(execution=execution_for()),
                EvaluationSuiteTarget(
                    execution=EvaluationExecution(
                        prompt="Write a welcome message",
                        temperature=0.7,
                        max_tokens=2048,
                    )
                ),
            ],
            db=None,
        )


# ---------------------------------------------------------------------------
# Import isolation: fresh interpreter, no app.ai leakage
# ---------------------------------------------------------------------------


def test_suite_import_and_run_in_fresh_interpreter():
    """Importing app.evaluation (incl. the suite) must not pull app.ai; a MODE B suite runs clean."""
    script = textwrap.dedent(
        f"""
        import sys
        import uuid
        from types import SimpleNamespace

        import app.evaluation  # noqa: F401
        from app.evaluation import (
            MAX_SUITE_TARGETS,
            EvaluationSuiteService,
            EvaluationSuiteTarget,
        )
        from app.evaluation.service import EvaluationService
        from app.evaluation.types import EvaluationExecution, EvaluationResult, EvaluatorConfig, Rule

        class DuckTestService:
            def run(self, **kwargs):
                return SimpleNamespace(
                    output="Simulated answer to: Write a welcome message",
                    provider="duck",
                    model="duck-1",
                    finish_reason="stop",
                    latency_ms=9,
                    usage=SimpleNamespace(
                        prompt_tokens=3, completion_tokens=4, total_tokens=7
                    ),
                    request_id=uuid.uuid4(),
                    prompt_id=None,
                    run_id=None,
                )

        rules = [Rule(type="contains", text="Simulated answer")]
        service = EvaluationSuiteService(EvaluationService(DuckTestService()))
        result = service.run_suite(
            evaluator=EvaluatorConfig(rules=rules),
            targets=[
                EvaluationSuiteTarget(
                    execution=EvaluationExecution(prompt="Write a welcome message")
                ),
                EvaluationSuiteTarget(
                    execution=EvaluationExecution(prompt="Write a thank-you note")
                ),
            ],
            db=None,
        )
        assert result.total == 2, result
        assert result.passed == 2, result
        assert MAX_SUITE_TARGETS == 20

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
        f"app.evaluation imported the AI stack or the suite failed: {result.stdout}{result.stderr}"
    )
    assert "CLEAN" in result.stdout