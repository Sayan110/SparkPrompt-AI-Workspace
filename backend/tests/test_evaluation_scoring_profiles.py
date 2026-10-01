"""Phase 3L scoring-profile tests: weighted deterministic scoring.

Matrix A-AF. Pure tests (A-S) run with no database, provider, or network —
only the real Pydantic contracts and the stdlib-only scoring helpers. API
tests (T-AA) use the conftest ``app_client`` (fake provider) plus the module
``db_ready`` skip pattern for anything touching Postgres. Frozen-phase
regressions (AB-AF) are asserted at contract level here; the full suite run
proves them wholesale.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal, engine
from app.evaluation.scoring import MAX_WEIGHT, score_evaluation, validate_scoring_coverage
from app.evaluation.types import RuleWeight, ScoringProfile

pytestmark = pytest.mark.integration


def _db_reachable() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(select(1))
        return True
    except SQLAlchemyError as exc:
        print(f"  - DB unreachable, skipping integration: {exc}")
        return False


@pytest.fixture(scope="module")
def db_ready() -> bool:
    return _db_reachable()


def _require_db(db_ready: bool) -> None:
    if not db_ready:
        pytest.skip("Postgres not reachable")


def _verdicts(flags: list[bool], ids: list[str] | None = None) -> list[dict]:
    ids = ids if ids is not None else [f"rule-{index}" for index in range(len(flags))]
    return [{"passed": passed, "rule_id": rule_id} for passed, rule_id in zip(flags, ids, strict=True)]


def _weighted(ids_weights: list[tuple[str, float]], mode: str = "weighted") -> ScoringProfile:
    return ScoringProfile(
        mode=mode,
        weights=[RuleWeight(rule_id=rule_id, weight=weight) for rule_id, weight in ids_weights],
    )


def _unweighted() -> ScoringProfile:
    return ScoringProfile()


# ------------------------------------------------- A-D: unweighted preserved


@pytest.mark.parametrize(
    ("flags", "expected", "label"),
    [
        ([False] * 4, 0.0, "A. unweighted 0/4"),
        ([True] + [False] * 3, 25.0, "B. unweighted 1/4"),
        ([True] * 3 + [False], 75.0, "C. unweighted 3/4"),
        ([True] * 4, 100.0, "D. unweighted 4/4"),
    ],
)
def test_unweighted_matrix(flags, expected, label):
    assert score_evaluation(_verdicts(flags), _unweighted()) == expected, label
    assert score_evaluation(_verdicts(flags)) == expected, label + " (omitted profile)"


def test_unweighted_ignores_weights():
    """Step 8: unweighted mode never consults the weight map."""
    profile = _weighted([("rule-0", 100.0), ("rule-1", 100.0)], mode="unweighted")
    assert score_evaluation(_verdicts([True, False]), profile) == 50.0


# ------------------------------------------------- E-I: weighted values


def test_weighted_all_pass():
    """E. every weight counts -> 100."""
    profile = _weighted([("a", 1), ("b", 3), ("c", 2)])
    assert score_evaluation(_verdicts([True, True, True], ["a", "b", "c"]), profile) == 100.0


def test_weighted_none_pass():
    """F. nothing passed -> 0."""
    profile = _weighted([("a", 1), ("b", 3), ("c", 2)])
    assert score_evaluation(_verdicts([False, False, False], ["a", "b", "c"]), profile) == 0.0


def test_weighted_mixed_spec_example():
    """G. spec example: passed weight 4 / total 6 -> 66.67."""
    profile = _weighted([("a", 1), ("b", 3), ("c", 2)])
    assert score_evaluation(_verdicts([True, True, False], ["a", "b", "c"]), profile) == 66.67


def test_weighted_fractional():
    """H. passed weight 2 / total 3 -> 66.67; single-weight pass -> 33.33."""
    profile = _weighted([("a", 1), ("b", 2)])
    assert score_evaluation(_verdicts([True, True], ["a", "b"]), profile) == 100.0
    assert score_evaluation(_verdicts([True, False], ["a", "b"]), profile) == 33.33
    assert score_evaluation(_verdicts([False, True], ["a", "b"]), profile) == 66.67


def test_weighted_two_decimal_rounding():
    """I. same SCORE_PRECISION=2 policy, no second precision system."""
    profile = _weighted([("a", 1), ("b", 1), ("c", 1)])
    assert score_evaluation(_verdicts([True, False, False], ["a", "b", "c"]), profile) == 33.33
    assert str(score_evaluation(_verdicts([True, False, False], ["a", "b", "c"]), profile)) == "33.33"


def test_weight_does_not_change_pass_fail():
    """Step 2: the helper reads verdicts; it cannot flip them."""
    profile = _weighted([("a", 1000), ("b", 0.5)])
    assert score_evaluation(_verdicts([False, True], ["a", "b"]), profile) == round(
        0.5 / 1000.5 * 100, 2
    )


# ------------------------------------------------- J: zero verdicts


def test_zero_verdicts_scores_null_in_both_modes():
    """J. no denominator -> None, never 0, in either mode."""
    assert score_evaluation([], _unweighted()) is None
    assert score_evaluation([], _weighted([("a", 1)])) is None


# ------------------------------------------------- K-S: validation


def test_zero_weight_rejected():
    """K."""
    with pytest.raises(ValidationError):
        RuleWeight(rule_id="a", weight=0)


def test_negative_weight_rejected():
    """L."""
    with pytest.raises(ValidationError):
        RuleWeight(rule_id="a", weight=-2.5)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_weight_rejected(bad):
    """M/N. NaN and both infinities fail FiniteFloat validation."""
    with pytest.raises(ValidationError):
        RuleWeight(rule_id="a", weight=bad)


def test_unknown_rule_id_rejected():
    """O."""
    with pytest.raises(ValueError, match="unknown rule ids"):
        validate_scoring_coverage(["a", "b"], _weighted([("a", 1), ("zzz", 2)]))


def test_missing_rule_id_rejected():
    """P."""
    with pytest.raises(ValueError, match="missing weights"):
        validate_scoring_coverage(["a", "b"], _weighted([("a", 1)]))


def test_duplicate_weight_definition_rejected():
    """Q. list-form weights keep duplicates detectable (a dict could not)."""
    with pytest.raises(ValidationError, match="duplicate weight"):
        ScoringProfile(
            mode="weighted",
            weights=[RuleWeight(rule_id="a", weight=1), RuleWeight(rule_id="a", weight=2)],
        )


def test_excessive_weight_rejected_and_bound_accepted():
    """R. MAX_WEIGHT bounds pathological magnitudes."""
    with pytest.raises(ValidationError):
        RuleWeight(rule_id="a", weight=MAX_WEIGHT + 1)
    assert RuleWeight(rule_id="a", weight=MAX_WEIGHT).weight == float(MAX_WEIGHT)


def test_weighted_mode_requires_rule_ids():
    """Rules without ids cannot be keyed in weighted mode."""
    with pytest.raises(ValueError, match="requires every rule to have an id"):
        validate_scoring_coverage(["a", None], _weighted([("a", 1), ("b", 1)]))


def test_duplicate_rule_ids_in_evaluator_rejected():
    """Ambiguous mapping (two rules, one id) is invalid in weighted mode."""
    with pytest.raises(ValueError, match="unique rule ids"):
        validate_scoring_coverage(["a", "a"], _weighted([("a", 1)]))


def test_unweighted_skips_coverage():
    """Unweighted mode never validates the map, even when it is nonsense."""
    validate_scoring_coverage(["a"], _weighted([("zzz", 1)], mode="unweighted"))
    validate_scoring_coverage([None], _unweighted())


def test_deterministic_repeated_calculation():
    """S. same inputs -> same outputs, every time, both modes."""
    profile = _weighted([("a", 1), ("b", 3), ("c", 2)])
    verdicts = _verdicts([True, False, True], ["a", "b", "c"])
    assert [score_evaluation(verdicts, profile) for _ in range(5)] == [50.0] * 5
    assert [score_evaluation(verdicts) for _ in range(5)] == [66.67] * 5


def test_score_bounds_hold_weighted():
    """Weighted scores stay within 0-100 and finite across magnitudes."""
    import math

    profile = _weighted([("a", 0.5), ("b", 1000.0)])
    for flags in ([True, True], [True, False], [False, True], [False, False]):
        score = score_evaluation(_verdicts(flags, ["a", "b"]), profile)
        assert score is not None and math.isfinite(score) and 0 <= score <= 100


# ------------------------------------------------- T-W: evaluation API


def _rules_abc() -> list[dict]:
    return [
        {"id": "rule-a", "type": "contains", "text": "Simulated answer"},
        {"id": "rule-b", "type": "min_length", "length": 5},
        {"id": "rule-c", "type": "contains", "text": "NoSuchMarkerXYZ"},
    ]


def _weights_abc() -> list[dict]:
    return [
        {"rule_id": "rule-a", "weight": 1},
        {"rule_id": "rule-b", "weight": 3},
        {"rule_id": "rule-c", "weight": 2},
    ]


def test_omitted_scoring_preserves_3k(app_client):
    """T. no scoring field -> exact Phase 3K result."""
    response = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {"prompt": "Write a welcome message"},
            "evaluator": {"rules": _rules_abc()},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [v["passed"] for v in body["verdicts"]] == [True, True, False]
    assert body["passed"] is False
    assert body["score"] == 66.67
    assert body["scoring"]["mode"] == "unweighted"


def test_explicit_unweighted_equals_default(app_client):
    """U. explicit unweighted == omitted scoring."""
    payload = {
        "execution": {"prompt": "Write a welcome message"},
        "evaluator": {"rules": _rules_abc()},
    }
    default = app_client.post("/api/evaluations/run", json=payload).json()
    explicit = app_client.post(
        "/api/evaluations/run", json={**payload, "scoring": {"mode": "unweighted"}}
    ).json()
    assert explicit["score"] == default["score"] == 66.67
    assert explicit["passed"] == default["passed"] is False
    assert explicit["verdicts"] == default["verdicts"]


def test_weighted_request_produces_expected_score(app_client):
    """V. 1+3 passed of 6 total -> 66.67 with the profile echoed."""
    response = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {"prompt": "Write a welcome message"},
            "evaluator": {"rules": _rules_abc()},
            "scoring": {"mode": "weighted", "weights": _weights_abc()},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["passed"] is False  # passed semantics untouched
    assert body["score"] == 66.67
    assert body["scoring"]["mode"] == "weighted"
    assert {
        (item["rule_id"], item["weight"]) for item in body["scoring"]["weights"]
    } == {("rule-a", 1.0), ("rule-b", 3.0), ("rule-c", 2.0)}


@pytest.mark.parametrize(
    ("scoring", "why"),
    [
        ({"mode": "weighted", "weights": [{"rule_id": "rule-a", "weight": 0}]}, "zero weight"),
        ({"mode": "weighted", "weights": [{"rule_id": "rule-a", "weight": -1}]}, "negative weight"),
        ({"mode": "weighted", "weights": [{"rule_id": "rule-a", "weight": 1001}]}, "excessive weight"),
        (
            {"mode": "weighted", "weights": [{"rule_id": "nope", "weight": 1}]},
            "unknown rule id",
        ),
        (
            {"mode": "weighted", "weights": [{"rule_id": "rule-a", "weight": 1}]},
            "missing rule weights",
        ),
        (
            {
                "mode": "weighted",
                "weights": [
                    {"rule_id": "rule-a", "weight": 1},
                    {"rule_id": "rule-a", "weight": 2},
                ],
            },
            "duplicate weight definition",
        ),
    ],
)
def test_malformed_scoring_is_422(app_client, scoring, why):
    """W. malformed scoring fails validation (422), whatever the provider state."""
    response = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {"prompt": "Write a welcome message"},
            "evaluator": {"rules": _rules_abc()},
            "scoring": scoring,
        },
    )
    assert response.status_code == 422, f"{why}: {response.text}"


def test_malformed_scoring_rejected_before_provider_contact(app_client, monkeypatch, settings):
    """W. with an exploding provider, malformed scoring is still 422, not 500."""
    from conftest import ExplodingProvider

    from app.ai.registry import ProviderRegistry
    from app.api.routes import ai as ai_routes

    registry = ProviderRegistry(settings)
    registry.register(ExplodingProvider(settings))
    monkeypatch.setattr(ai_routes, "_REGISTRY_CACHE", registry)

    response = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {"prompt": "Write a welcome message"},
            "evaluator": {"rules": _rules_abc()},
            "scoring": {"mode": "weighted", "weights": [{"rule_id": "rule-a", "weight": 0}]},
        },
    )
    assert response.status_code == 422, response.text


# ------------------------------------------------- X: tampered supplied score


def test_tampered_weighted_score_is_recomputed(app_client):
    """X. supplied score + profile are re-derived from supplied verdicts."""
    evaluated = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {"prompt": "Write a welcome message"},
            "evaluator": {"rules": _rules_abc()},
            "scoring": {"mode": "weighted", "weights": _weights_abc()},
        },
    ).json()
    assert evaluated["score"] == 66.67
    evaluated["score"] = 99.0  # tampered
    other = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {"prompt": "Write a farewell"},
            "evaluator": {"rules": _rules_abc()},
            "scoring": {"mode": "weighted", "weights": _weights_abc()},
        },
    ).json()

    response = app_client.post(
        "/api/comparisons/run",
        json={"left_evaluation": evaluated, "right_evaluation": other},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["left_execution"]["score"] == 66.67
    assert body["left_execution"]["scoring_mode"] == "weighted"
    assert body["right_execution"]["scoring_mode"] == "weighted"


def test_mixed_profile_modes_are_visible_not_silent(app_client):
    """Step 17: differently-profiled sides expose their modes factually."""
    left = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {"prompt": "Write a welcome message"},
            "evaluator": {"rules": _rules_abc()},
            "scoring": {"mode": "weighted", "weights": _weights_abc()},
        },
    ).json()
    right = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {"prompt": "Write a welcome message"},
            "evaluator": {"rules": _rules_abc()},
        },
    ).json()

    response = app_client.post(
        "/api/comparisons/run",
        json={"left_evaluation": left, "right_evaluation": right},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["left_execution"]["scoring_mode"] == "weighted"
    assert body["right_execution"]["scoring_mode"] == "unweighted"
    assert isinstance(body["metadata_diff"]["score_delta"], float)
    for forbidden in ("winner", "better", "ranking", "recommendation"):
        assert forbidden not in response.text.lower()


# ------------------------------------------------- Y/Z/AA: run ownership with scoring


def _create_foreign_run() -> str:
    """A run owned by a different user (evaluation must 404 it)."""
    from app.models import Project, Prompt, PromptRun, User

    with SessionLocal() as session:
        user = User(
            email=f"foreign-3l-{uuid.uuid4().hex[:10]}@sparkprompt.local",
            display_name="Foreign workspace owner",
        )
        session.add(user)
        session.flush()
        project = Project(user_id=user.id, name="Foreign workspace")
        session.add(project)
        session.flush()
        prompt = Prompt(project_id=project.id, title="Foreign prompt", idea="not yours")
        session.add(prompt)
        session.flush()
        run = PromptRun(
            prompt_id=prompt.id,
            provider="fake",
            model="fake-model-1",
            output_text="foreign secret output",
            status="success",
        )
        session.add(run)
        session.commit()
        return str(run.id)


def test_owned_run_weighted_evaluation(app_client, db_ready):
    """Y. MODE A honors a weighted profile on an owned run."""
    _require_db(db_ready)
    created = app_client.post(
        "/api/prompts",
        json={"title": f"3L owned run {uuid.uuid4().hex[:8]}", "idea": "Weighted MODE A."},
    )
    assert created.status_code == 201, created.text
    tested = app_client.post(
        "/api/testing/run",
        json={"prompt": "Write a welcome message", "prompt_id": created.json()["id"]},
    )
    assert tested.status_code == 200, tested.text
    run_id = tested.json()["run_id"]
    assert run_id is not None

    response = app_client.post(
        "/api/evaluations/run",
        json={
            "run_id": run_id,
            "evaluator": {"rules": _rules_abc()},
            "scoring": {"mode": "weighted", "weights": _weights_abc()},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == run_id
    assert body["score"] == 66.67


def test_foreign_run_weighted_evaluation_is_404(app_client, db_ready):
    """Z. a foreign run fails identically; no score side-channel exists."""
    _require_db(db_ready)
    foreign_id = _create_foreign_run()
    response = app_client.post(
        "/api/evaluations/run",
        json={
            "run_id": foreign_id,
            "evaluator": {"rules": _rules_abc()},
            "scoring": {"mode": "weighted", "weights": _weights_abc()},
        },
    )
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Prompt run not found"}
    assert "score" not in response.text
    assert "foreign secret output" not in response.text


def test_unknown_run_weighted_evaluation_is_404(app_client):
    """AA."""
    response = app_client.post(
        "/api/evaluations/run",
        json={
            "run_id": str(uuid.uuid4()),
            "evaluator": {"rules": _rules_abc()},
            "scoring": {"mode": "weighted", "weights": _weights_abc()},
        },
    )
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Prompt run not found"}


# ------------------------------------------------- AB-AF: frozen-phase regression


def test_comparison_run_pair_stays_unweighted_default(app_client, db_ready):
    """AB. run-pair comparison re-evaluates unweighted; factual fields only."""
    _require_db(db_ready)
    created = app_client.post(
        "/api/prompts",
        json={"title": f"3L compare {uuid.uuid4().hex[:8]}", "idea": "Run-pair default."},
    )
    prompt_id = created.json()["id"]
    left_id = app_client.post(
        "/api/testing/run",
        json={"prompt": "Write a welcome message", "prompt_id": prompt_id},
    ).json()["run_id"]
    right_id = app_client.post(
        "/api/testing/run",
        json={"prompt": "Write a farewell", "prompt_id": prompt_id},
    ).json()["run_id"]

    response = app_client.post(
        "/api/comparisons/run",
        json={
            "left_run_id": left_id,
            "right_run_id": right_id,
            "evaluator": {"rules": _rules_abc()},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["left_execution"]["scoring_mode"] == "unweighted"
    assert body["right_execution"]["scoring_mode"] == "unweighted"
    assert body["left_execution"]["score"] == 66.67
    for forbidden in ("winner", "better", "ranking", "recommendation"):
        assert forbidden not in response.text.lower()


def test_suite_and_experiment_carry_default_profiles(app_client, db_ready):
    """AC/AD. suite + experiment aggregate as before; results carry unweighted scores."""
    _require_db(db_ready)
    suite = app_client.post(
        "/api/evaluations/suite",
        json={
            "evaluator": {"rules": _rules_abc()},
            "targets": [
                {"execution": {"prompt": "Write a welcome message"}},
                {"execution": {"prompt": "Write a farewell"}},
            ],
        },
    )
    assert suite.status_code == 200, suite.text
    assert suite.json()["total"] == 2
    assert all(item["scoring"]["mode"] == "unweighted" for item in suite.json()["evaluations"])

    created = app_client.post(
        "/api/prompts",
        json={
            "title": f"3L experiment {uuid.uuid4().hex[:8]}",
            "idea": "Experiment default profile.",
            "body": "Write a welcome message",
        },
    )
    prompt_id = created.json()["id"]
    experiment = app_client.post(
        "/api/experiments/run",
        json={"prompt_id": prompt_id, "evaluator": {"rules": _rules_abc()}},
    )
    assert experiment.status_code == 200, experiment.text
    body = experiment.json()
    assert body["total_versions"] == 1
    assert body["evaluations"][0]["score"] == 66.67
    assert body["evaluations"][0]["scoring"]["mode"] == "unweighted"


def test_version_flows_unchanged(app_client, db_ready):
    """AE/AF. 3I append + 3J history/restore behave exactly as before."""
    _require_db(db_ready)
    created = app_client.post(
        "/api/prompts",
        json={
            "title": f"3L versions {uuid.uuid4().hex[:8]}",
            "idea": "Scoring profiles must not touch versioning.",
            "body": "Write a short email",
        },
    )
    prompt_id = created.json()["id"]
    assert created.json()["version_number"] == 1
    assert app_client.put(f"/api/prompts/{prompt_id}", json={"body": "Write a long email"}).json()["version_number"] == 2
    history = app_client.get(f"/api/prompts/{prompt_id}/versions").json()
    assert [item["version_number"] for item in history] == [1, 2]
    v1_id = next(item["id"] for item in history if item["version_number"] == 1)
    restored = app_client.post(f"/api/prompts/{prompt_id}/versions/{v1_id}/restore")
    assert restored.status_code == 201, restored.text
    assert restored.json()["version_number"] == 3


def test_no_schema_change_from_profiles(db_ready):
    """Step 24/30. profiles live in requests/responses only: no tables, no columns."""
    _require_db(db_ready)
    from sqlalchemy import inspect as sa_inspect

    from app.core.database import Base
    from app.models import PromptRun, PromptVersion

    assert "scoring" not in PromptRun.__table__.columns
    assert "scoring" not in PromptVersion.__table__.columns
    assert "weight" not in PromptRun.__table__.columns
    # 4B: alembic_version is migration bookkeeping, not an app table (same
    # exclusion as test_migrations.py); any other extra table still fails here.
    assert set(sa_inspect(engine).get_table_names()) - {"alembic_version"} == set(
        Base.metadata.tables.keys()
    )
