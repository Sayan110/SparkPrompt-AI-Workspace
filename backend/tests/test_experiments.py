"""Phase 3H experiment tests: domain contracts + ``ExperimentService``.

Mirrors ``test_suite.py`` rigor: duck-typed versions/prompts/resolvers and a duck-typed
``PromptTestingService`` so the whole domain runs with no database, no provider, and no
network, plus an AI-free import surface asserted in a fresh interpreter and by an AST
walk. No live provider keys, no Ollama.
"""

import copy
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.evaluation import EvaluationResult, EvaluationService
from app.evaluation.types import EvaluationExecution, EvaluatorConfig, Rule
from app.experiments import (
    MAX_EXPERIMENT_VERSIONS,
    ExperimentResult,
    ExperimentService,
    ExperimentValidationError,
)
from app.services.errors import NotFoundError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MARKER = "marker-ok"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def rule(**overrides) -> Rule:
    defaults = {
        "id": "r1",
        "label": "marker rule",
        "type": "contains",
        "text": MARKER,
    }
    defaults.update(overrides)
    return Rule(**defaults)


def simple_evaluator(rules: list[Rule] | None = None, **overrides) -> EvaluatorConfig:
    values = {"name": "sample evaluator", "rules": rules or [rule()]}
    values.update(overrides)
    return EvaluatorConfig(**values)


def duck_version(version_number: int, body: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), version_number=version_number, body=body
    )


def duck_prompt(prompt_id=None) -> SimpleNamespace:
    return SimpleNamespace(id=prompt_id or uuid.uuid4(), title="duck prompt")


def duck_output(body: str) -> str:
    """Deterministic stand-in for a provider: a body with GOOD satisfies the marker."""
    return f"Simulated answer to: {body}{' marker-ok' if 'GOOD' in body else ''}"


class DuckTestService:
    """Duck PromptTestingService that records every call it receives."""

    def __init__(self):
        self.calls: list[dict] = []

    def run(self, **kwargs) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            output=duck_output(kwargs["prompt"]),
            provider="duck",
            model="duck-1",
            finish_reason="stop",
            latency_ms=5,
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            request_id=uuid.uuid4(),
            prompt_id=kwargs.get("prompt_id"),
            run_id=None,
        )


class SpyEvaluationService:
    """Records the exact kwargs the experiment passes to the composed service."""

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[dict] = []

    def evaluate(self, **kwargs):
        self.calls.append(kwargs)
        return self._inner.evaluate(**kwargs)


class FakeDB:
    """Serves duck versions via ``scalars`` and records the statement it was given."""

    def __init__(self, versions):
        self._versions = list(versions)
        self.statements: list = []
        self.scalar_calls = 0

    def scalars(self, statement):
        self.scalar_calls += 1
        self.statements.append(statement)
        return iter(self._versions)


def prompt_resolver_for(prompt, *, reject=()):
    """Ownership-scoped resolver: serves ``prompt``, NotFoundError for anything else."""
    rejected = set(reject)

    def resolver(db, prompt_id):
        if prompt_id == rejected or prompt_id != prompt.id:
            raise NotFoundError("Prompt not found")
        return prompt

    return resolver


def service_for(versions, *, prompt=None, test_service=None, reject=()):
    """Build an experiment service over duck versions (no database required)."""
    prompt = prompt or duck_prompt()
    duck = test_service if test_service is not None else DuckTestService()
    spy = SpyEvaluationService(EvaluationService(duck))
    service = ExperimentService(
        spy, prompt_resolver=prompt_resolver_for(prompt, reject=reject)
    )
    db = FakeDB(versions)
    return service, db, duck, spy, prompt


# ---------------------------------------------------------------------------
# Domain contracts
# ---------------------------------------------------------------------------


def test_max_experiment_versions_ceiling():
    assert MAX_EXPERIMENT_VERSIONS == 20


def test_experiment_result_counts_are_integers_only():
    result = ExperimentResult(
        total_versions=2,
        passed=1,
        evaluations=[
            EvaluationResult(
                run_id=None,
                output="out",
                provider="duck",
                model="duck-1",
                latency_ms=1,
                usage=None,
                evaluator_snapshot=simple_evaluator(),
                verdicts=[],
                passed=False,
            )
            for _ in range(2)
        ],
    )
    assert result.total_versions == 2
    assert result.passed == 1
    assert isinstance(result.total_versions, int)
    assert isinstance(result.passed, int)
    assert len(result.evaluations) == 2
    assert result.generated_at is not None


def test_experiment_result_rejects_forbidden_aggregate_fields():
    """No score / ratio / grade / ranking / winner may be smuggled into the contract."""
    forbidden = (
        "score",
        "percentage",
        "ratio",
        "grade",
        "ranking",
        "winner",
        "recommendation",
        "improvement_score",
        "baseline_score",
        "pass_rate",
    )
    assert set(ExperimentResult.model_fields) == {
        "total_versions",
        "passed",
        "evaluations",
        "generated_at",
    }
    for name in forbidden:
        assert name not in ExperimentResult.model_fields


# ---------------------------------------------------------------------------
# Domain behavior: versions, counts, ordering
# ---------------------------------------------------------------------------


def test_single_version_prompt():
    """(1) A prompt with one persisted version yields one evaluation."""
    service, _db, duck, _spy, prompt = service_for(
        [duck_version(1, "Write a welcome message")], prompt=duck_prompt()
    )
    result = service.run(prompt_id=prompt.id, evaluator=simple_evaluator(), db=_db)
    assert result.total_versions == 1
    assert len(result.evaluations) == 1
    assert len(duck.calls) == 1


def test_multi_version_prompt():
    """(2) Every persisted version is evaluated."""
    service, db, duck, _spy, prompt = service_for(
        [duck_version(n, f"v{n} body GOOD") for n in (1, 2, 3, 4)]
    )
    result = service.run(prompt_id=prompt.id, evaluator=simple_evaluator(), db=db)
    assert result.total_versions == 4
    assert len(result.evaluations) == 4
    assert len(duck.calls) == 4


def test_zero_versions_is_a_factual_empty_measurement():
    """(3) A prompt with no versions measures zero — it is not an error."""
    service, db, duck, _spy, prompt = service_for([])
    result = service.run(prompt_id=prompt.id, evaluator=simple_evaluator(), db=db)
    assert result.total_versions == 0
    assert result.passed == 0
    assert result.evaluations == []
    # No version to read and no provider to contact.
    assert duck.calls == []


def test_versions_are_returned_in_ascending_version_order():
    """(4) Ordered input stays in ascending version order."""
    versions = [duck_version(n, f"v{n} body") for n in (1, 2, 3)]
    service, db, _duck, _spy, prompt = service_for(versions)
    result = service.run(prompt_id=prompt.id, evaluator=simple_evaluator(), db=db)
    outputs = [evaluation.output for evaluation in result.evaluations]
    assert outputs == [
        duck_output("v1 body"),
        duck_output("v2 body"),
        duck_output("v3 body"),
    ]


def test_ordering_is_driven_by_version_number_not_row_order():
    """(5) The SQL itself orders by version_number — never insertion/UUID/created_at."""
    service, db, _duck, _spy, prompt = service_for([])
    service.run(prompt_id=prompt.id, evaluator=simple_evaluator(), db=db)
    statement = str(db.statements[0])
    order_by = statement.split("ORDER BY")[-1]
    assert "prompt_versions.version_number ASC" in order_by
    # created_at must never be the ordering mechanism.
    assert "created_at" not in order_by


def test_versions_supplied_out_of_order_are_evaluated_in_supplied_order():
    """(5b) The service preserves whatever order the ordered query produced.

    The database is responsible for the sort; the service evaluates strictly in the
    order it receives versions, so a descending-ordered source would be reflected
    verbatim rather than silently re-sorted.
    """
    versions = [duck_version(3, "third"), duck_version(1, "first"), duck_version(2, "second")]
    service, db, _duck, _spy, prompt = service_for(versions)
    result = service.run(prompt_id=prompt.id, evaluator=simple_evaluator(), db=db)
    assert [evaluation.output for evaluation in result.evaluations] == [
        duck_output("third"),
        duck_output("first"),
        duck_output("second"),
    ]


def test_exactly_maximum_versions_is_allowed():
    """(6) The bound is inclusive."""
    versions = [duck_version(n, f"v{n} GOOD") for n in range(1, MAX_EXPERIMENT_VERSIONS + 1)]
    service, db, _duck, _spy, prompt = service_for(versions)
    result = service.run(prompt_id=prompt.id, evaluator=simple_evaluator(), db=db)
    assert result.total_versions == MAX_EXPERIMENT_VERSIONS


def test_oversized_version_count_is_rejected_cleanly():
    """(7) Oversized -> clean validation error, never truncation or recency selection."""
    versions = [duck_version(n, f"v{n} GOOD") for n in range(1, MAX_EXPERIMENT_VERSIONS + 2)]
    service, db, duck, _spy, prompt = service_for(versions)
    with pytest.raises(ExperimentValidationError) as excinfo:
        service.run(prompt_id=prompt.id, evaluator=simple_evaluator(), db=db)
    assert str(MAX_EXPERIMENT_VERSIONS) in str(excinfo.value)
    # Nothing was executed and nothing was silently dropped.
    assert duck.calls == []


def test_service_requires_a_database_session():
    service, _db, _duck, _spy, prompt = service_for([duck_version(1, "v1")])
    with pytest.raises(ExperimentValidationError):
        service.run(prompt_id=prompt.id, evaluator=simple_evaluator(), db=None)


# ---------------------------------------------------------------------------
# Domain behavior: evaluator validation is reused from Phase 3E
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rules",
    [
        [],
        [{"id": "r1", "label": "marker rule", "type": "contains", "text": MARKER}] * 21,
        [{"type": "contains"}],
        [{"type": "min_length"}],
        [{"type": "regex_match"}],
        [{"type": "regex_match", "pattern": "(unclosed"}],
    ],
)
def test_malformed_evaluator_is_rejected(rules):
    """(9) Existing 3E rule validation still rejects malformed evaluators."""
    with pytest.raises(ValidationError):
        EvaluatorConfig(rules=[Rule(**item) for item in rules])


def test_evaluator_requires_expected_output_for_match_rules():
    with pytest.raises(ValidationError):
        EvaluatorConfig(rules=[Rule(type="exact_match")])


def test_valid_evaluator_is_accepted_unchanged():
    """(8) The experiment reuses EvaluatorConfig verbatim."""
    evaluator = simple_evaluator([rule()], expected_output="golden")
    result_evaluator = ExperimentResult(
        total_versions=0,
        passed=0,
        evaluations=[
            EvaluationResult(
                output="out",
                evaluator_snapshot=evaluator,
                verdicts=[],
                passed=True,
            )
        ],
    )
    assert result_evaluator.evaluations[0].evaluator_snapshot == evaluator


# ---------------------------------------------------------------------------
# Domain behavior: counts
# ---------------------------------------------------------------------------


def test_total_versions_matches_versions_evaluated():
    """(10) total_versions is exact."""
    versions = [duck_version(n, f"v{n}") for n in range(1, 6)]
    service, db, _duck, _spy, prompt = service_for(versions)
    result = service.run(prompt_id=prompt.id, evaluator=simple_evaluator(), db=db)
    assert result.total_versions == len(versions)
    assert result.total_versions == len(result.evaluations)


def test_passed_counts_mixed_pass_and_fail():
    """(11)+(12) Mixed PASS/FAIL: only the GOOD versions pass."""
    versions = [
        duck_version(1, "alpha GOOD"),
        duck_version(2, "beta"),
        duck_version(3, "gamma GOOD"),
    ]
    service, db, _duck, _spy, prompt = service_for(versions)
    result = service.run(prompt_id=prompt.id, evaluator=simple_evaluator(), db=db)
    assert result.total_versions == 3
    assert result.passed == 2
    assert [evaluation.passed for evaluation in result.evaluations] == [True, False, True]


def test_all_versions_pass():
    """(13) All PASS."""
    versions = [duck_version(n, f"v{n} GOOD") for n in (1, 2, 3)]
    service, db, _duck, _spy, prompt = service_for(versions)
    result = service.run(prompt_id=prompt.id, evaluator=simple_evaluator(), db=db)
    assert result.passed == 3
    assert all(evaluation.passed for evaluation in result.evaluations)


def test_no_versions_pass():
    """(14) All FAIL."""
    versions = [duck_version(n, f"v{n}") for n in (1, 2, 3)]
    service, db, _duck, _spy, prompt = service_for(versions)
    result = service.run(prompt_id=prompt.id, evaluator=simple_evaluator(), db=db)
    assert result.passed == 0
    assert not any(evaluation.passed for evaluation in result.evaluations)


def test_per_version_results_are_the_existing_evaluation_result():
    """(15) Parity: identical to a direct EvaluationService evaluation."""
    evaluator = simple_evaluator()
    versions = [duck_version(1, "alpha GOOD"), duck_version(2, "beta")]

    service, db, _duck, _spy, prompt = service_for(versions)
    result = service.run(prompt_id=prompt.id, evaluator=evaluator, db=db)

    direct = EvaluationService(DuckTestService())
    for version, evaluation in zip(versions, result.evaluations):
        assert isinstance(evaluation, EvaluationResult)
        expected = direct.evaluate(
            evaluator=evaluator,
            execution=EvaluationExecution(prompt=version.body, prompt_id=prompt.id),
            db=None,
        )
        assert evaluation.model_dump(exclude={"generated_at"}) == expected.model_dump(
            exclude={"generated_at"}
        )


# ---------------------------------------------------------------------------
# Domain behavior: fresh execution per version
# ---------------------------------------------------------------------------


def test_exact_version_body_is_forwarded_verbatim():
    """(16) The stored body is passed through untouched — no trimming or templating."""
    raw = "\n  Act as a copywriter.\n\nWrite a welcome message.  \n"
    service, db, duck, _spy, prompt = service_for([duck_version(1, raw)])
    service.run(prompt_id=prompt.id, evaluator=simple_evaluator(), db=db)
    assert duck.calls[0]["prompt"] == raw


def test_every_version_is_a_fresh_execution():
    """(17) Each version becomes a NEW execution (never an existing run_id)."""
    versions = [duck_version(n, f"v{n} GOOD") for n in (1, 2, 3)]
    service, db, duck, spy, prompt = service_for(versions)
    result = service.run(prompt_id=prompt.id, evaluator=simple_evaluator(), db=db)

    # One distinct fresh execution per version, in order.
    assert [call["prompt"] for call in duck.calls] == [v.body for v in versions]
    assert len(duck.calls) == 3
    # MODE B was used for every version: execution set, run_id never supplied.
    assert len(spy.calls) == 3
    for call in spy.calls:
        assert isinstance(call["execution"], EvaluationExecution)
        assert call.get("run_id") is None
    # The existing gateway mechanism carries the prompt id so a normal PromptRun may
    # be recorded; the experiment itself persists nothing.
    assert {call["prompt_id"] for call in duck.calls} == {prompt.id}
    assert len(result.evaluations) == 3


def test_version_records_are_never_mutated():
    """(18) Reading versions for measurement never writes to them."""
    versions = [duck_version(1, "alpha GOOD"), duck_version(2, "beta")]
    before = copy.deepcopy(versions)
    service, db, _duck, _spy, prompt = service_for(versions)
    service.run(prompt_id=prompt.id, evaluator=simple_evaluator(), db=db)
    assert versions == before
    # The service issued exactly one read statement and no writes.
    assert db.scalar_calls == 1
    assert "UPDATE" not in str(db.statements[0]).upper()
    assert "DELETE" not in str(db.statements[0]).upper()
    assert "INSERT" not in str(db.statements[0]).upper()


def test_unusable_version_body_is_rejected_before_any_execution():
    """A stored version that cannot be executed fails cleanly, with no partial work."""
    versions = [duck_version(1, "alpha GOOD"), duck_version(2, "   ")]
    service, db, duck, _spy, prompt = service_for(versions)
    with pytest.raises(ExperimentValidationError) as excinfo:
        service.run(prompt_id=prompt.id, evaluator=simple_evaluator(), db=db)
    assert "Version 2" in str(excinfo.value)
    # The usable version 1 was never executed — no half-finished experiment.
    assert duck.calls == []


# ---------------------------------------------------------------------------
# Ownership: first, and absolute
# ---------------------------------------------------------------------------


def test_unknown_prompt_raises_not_found():
    """(19) An unknown prompt fails with the standard prompt-not-found error."""
    service, db, duck, spy, _prompt = service_for(
        [duck_version(1, "alpha")], reject=[uuid.uuid4()]
    )
    unknown = uuid.uuid4()
    with pytest.raises(NotFoundError) as excinfo:
        service.run(prompt_id=unknown, evaluator=simple_evaluator(), db=db)
    assert str(excinfo.value) == "Prompt not found"
    assert duck.calls == []
    assert spy.calls == []


def test_foreign_prompt_raises_not_found_and_leaks_nothing():
    """(20) A foreign prompt is indistinguishable from an unknown one."""
    service, db, duck, _spy, _prompt = service_for(
        [duck_version(1, "alpha")], reject=[uuid.uuid4()]
    )
    foreign = uuid.uuid4()
    with pytest.raises(NotFoundError) as excinfo:
        service.run(prompt_id=foreign, evaluator=simple_evaluator(), db=db)
    # Same message as the unknown case — no existence leak, no foreign metadata.
    assert str(excinfo.value) == "Prompt not found"
    assert duck.calls == []


def test_nothing_happens_before_ownership_is_resolved():
    """(21)+(22) No version read, no execution, no partial output."""
    service, db, duck, spy, _prompt = service_for(
        [duck_version(n, f"v{n} GOOD") for n in (1, 2, 3)], reject=[uuid.uuid4()]
    )
    with pytest.raises(NotFoundError):
        service.run(prompt_id=uuid.uuid4(), evaluator=simple_evaluator(), db=db)
    # Ownership is checked BEFORE any version is read.
    assert db.scalar_calls == 0
    # And before any provider is contacted.
    assert duck.calls == []
    assert spy.calls == []


# ---------------------------------------------------------------------------
# Import isolation: fresh interpreter + AST walk
# ---------------------------------------------------------------------------

_FORBIDDEN = ("gemini", "nvidia", "ollama", "providers", "gateway", "router", "registry")


def test_experiments_never_imports_ai_stack_statically():
    """(26) No app/experiments file may import the provider/gateway/router/registry."""
    experiments_dir = PROJECT_ROOT / "app" / "experiments"
    files = sorted(experiments_dir.rglob("*.py"))
    assert files, "app/experiments must contain Python modules"
    import ast

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


def test_experiments_import_and_run_in_fresh_interpreter():
    """(23)(24)(25) Fresh interpreter: no app.ai / provider adapter / gateway leakage."""
    script = textwrap.dedent(
        f"""
        import sys
        import uuid
        from types import SimpleNamespace

        import app.experiments  # noqa: F401
        from app.experiments import (
            MAX_EXPERIMENT_VERSIONS,
            ExperimentService,
        )
        from app.evaluation import EvaluationService
        from app.evaluation.types import EvaluatorConfig, Rule

        class DuckTestService:
            def run(self, **kwargs):
                return SimpleNamespace(
                    output="Simulated answer to: " + kwargs["prompt"] + " marker-ok",
                    provider="duck",
                    model="duck-1",
                    finish_reason="stop",
                    latency_ms=4,
                    usage=SimpleNamespace(
                        prompt_tokens=1, completion_tokens=1, total_tokens=2
                    ),
                    request_id=uuid.uuid4(),
                    prompt_id=kwargs.get("prompt_id"),
                    run_id=None,
                )

        class FakeDB:
            def __init__(self, versions):
                self._versions = versions

            def scalars(self, statement):
                return iter(self._versions)

        prompt_id = uuid.uuid4()
        versions = [
            SimpleNamespace(id=uuid.uuid4(), version_number=1, body="v1 GOOD"),
            SimpleNamespace(id=uuid.uuid4(), version_number=2, body="v2 GOOD"),
        ]
        service = ExperimentService(
            EvaluationService(DuckTestService()),
            prompt_resolver=lambda db, pid: SimpleNamespace(id=pid),
        )
        result = service.run(
            prompt_id=prompt_id,
            evaluator=EvaluatorConfig(rules=[Rule(type="contains", text="marker-ok")]),
            db=FakeDB(versions),
        )
        assert result.total_versions == 2, result
        assert result.passed == 2, result
        assert MAX_EXPERIMENT_VERSIONS == 20

        leaked = sorted(m for m in sys.modules if m.startswith("app.ai"))
        if leaked:
            print("LEAKED:" + ",".join(leaked))
            sys.exit(1)
        for forbidden in ("app.ai.providers", "app.ai.gateway", "app.ai.registry", "app.ai.router"):
            assert forbidden not in sys.modules, forbidden
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
        f"app.experiments imported the AI stack or the experiment failed: "
        f"{result.stdout}{result.stderr}"
    )
    assert "CLEAN" in result.stdout
