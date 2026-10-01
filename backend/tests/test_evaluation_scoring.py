"""Phase 3K deterministic scoring tests: verdicts -> numeric score.

Matrix A-X. Pure-helper tests (A-M, architecture) run with no database, no
FastAPI, no provider, and no network — proving the scoring layer is truly
deterministic. API tests (N-S) use the conftest ``app_client`` (fake provider)
plus the module ``db_ready`` skip pattern for anything touching Postgres.
Frozen-phase regressions (T-X) are asserted here at the contract level; the
full suite run proves them wholesale.
"""

from __future__ import annotations

import ast
import math
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal, engine
from app.evaluation.scoring import (
    SCORE_MAX,
    SCORE_MIN,
    SCORE_PRECISION,
    score_evaluation,
)

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


def _verdicts(passes: list[bool]) -> list[dict]:
    """Minimal verdict-shaped mappings; the helper only reads ``passed``."""
    return [{"passed": passed} for passed in passes]


# ------------------------------------------------- A-G: canonical score values


@pytest.mark.parametrize(
    ("passes", "expected", "label"),
    [
        ([False], 0.0, "A. 0/1 -> 0"),
        ([True], 100.0, "B. 1/1 -> 100"),
        ([True, False], 50.0, "C. 1/2 -> 50"),
        ([True, True, False], 66.67, "D. 2/3 -> 66.67"),
        ([True, True, True, False], 75.0, "E. 3/4 -> 75"),
        ([True, True, True, True], 100.0, "F. 4/4 -> 100"),
        ([True, True, True, False, False, False, False], 42.86, "G. 3/7 -> 42.86"),
    ],
)
def test_canonical_scores(passes, expected, label):
    assert score_evaluation(_verdicts(passes)) == expected, label


# ------------------------------------------------- H: zero verdicts -> null


def test_no_verdicts_scores_null():
    """(H) No denominator means no score — never 0."""
    assert score_evaluation([]) is None
    assert score_evaluation(None) is None


def test_empty_evaluator_is_rejected_at_the_boundary(app_client):
    """(H) The wire contract still requires >= 1 rule, so a null score can only
    arise from the helper level, never from a boundary-evading request."""
    response = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {"prompt": "Write a welcome message"},
            "evaluator": {"name": "empty", "rules": []},
        },
    )
    assert response.status_code == 422, response.text


# ------------------------------------------------- I-M: numeric hygiene


def test_score_is_always_finite_and_bounded():
    """(I)(J)(K)(L) Exhaustive small denominators: finite, 0 <= score <= 100."""
    for total in range(1, 21):
        for passed_count in range(total + 1):
            verdicts = _verdicts([True] * passed_count + [False] * (total - passed_count))
            score = score_evaluation(verdicts)
            assert score is not None
            assert math.isfinite(score)
            assert SCORE_MIN <= score <= SCORE_MAX


def test_exact_rounding_behavior():
    """(M) Stable 2-decimal rounding with no float artifacts in the text form."""
    assert score_evaluation(_verdicts([True, False, False])) == 33.33  # 1/3
    assert score_evaluation(_verdicts([True] * 5 + [False])) == 83.33  # 5/6
    assert score_evaluation(_verdicts([True] + [False] * 5)) == 16.67  # 1/6
    for total in (1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 13, 20):
        for passed_count in range(total + 1):
            text = str(score_evaluation(_verdicts([True] * passed_count + [False] * (total - passed_count))))
            decimals = text.split(".")[1] if "." in text else ""
            assert len(decimals) <= SCORE_PRECISION, text


def test_score_counts_only_passed_flags():
    """Verdict order and extra keys do not affect the score."""
    assert score_evaluation(_verdicts([False, True, False, True])) == 50.0
    assert score_evaluation([{"passed": True, "type": "contains", "evidence": {}}]) == 100.0


# ------------------------------------------------- architecture: purity


def test_scoring_module_is_dependency_free():
    """(Step 23) scoring.py imports stdlib only — no AI, DB, or provider imports."""
    from app.evaluation import scoring

    source = Path(scoring.__file__).read_text()
    tree = ast.parse(source)
    stdlib = {
        "__future__",
        "annotations",
        "typing",
        "math",
        "dataclasses",
        "enum",
        "functools",
        "itertools",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in stdlib, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports"
            assert (node.module or "").split(".")[0] in stdlib, node.module
    assert "app.ai" not in source
    assert "app.testing" not in source
    assert "sqlalchemy" not in source


def test_scoring_never_touches_ai_or_db():
    """The helper works on plain data with no fixtures at all (proof by doing)."""
    assert score_evaluation([{"passed": True}] * 3 + [{"passed": False}]) == 75.0


# ------------------------------------------------- N/O: passed + verdicts preserved


def test_result_derives_score_without_changing_verdicts(app_client):
    """(N)(O) passed stays the AND, verdicts stay exactly what the checkers say."""
    payload = {
        "execution": {"prompt": "Write a welcome message"},
        "evaluator": {
            "rules": [
                {"type": "contains", "text": "Simulated answer"},
                {"type": "contains", "text": "Java"},
                {"type": "min_length", "length": 10},
            ]
        },
    }
    response = app_client.post("/api/evaluations/run", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [v["passed"] for v in body["verdicts"]] == [True, False, True]
    assert body["passed"] is False
    assert body["score"] == 66.67
    assert body["verdicts"][0]["evidence"] == {"matched": True, "text": "Simulated answer"}


def test_all_pass_means_score_100(app_client):
    """(N) passed=true pairs with score 100, not a replacement of it."""
    response = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {"prompt": "Write a welcome message"},
            "evaluator": {"rules": [{"type": "contains", "text": "Simulated answer"}]},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["passed"] is True
    assert body["score"] == 100.0


# ------------------------------------------------- P: inline evaluation score


def test_inline_evaluation_score_matrix(app_client):
    """(P) MODE B scores across several pass/total combinations."""
    cases = [
        ([{"type": "contains", "text": "Nope"}], 0.0, False),
        (
            [
                {"type": "contains", "text": "Simulated answer"},
                {"type": "min_length", "length": 10},
                {"type": "contains", "text": "Nope"},
                {"type": "max_length", "length": 10_000},
            ],
            75.0,
            False,
        ),
    ]
    for rules, expected_score, expected_passed in cases:
        response = app_client.post(
            "/api/evaluations/run",
            json={"execution": {"prompt": "Write a welcome message"}, "evaluator": {"rules": rules}},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["score"] == expected_score, rules
        assert body["passed"] is expected_passed, rules


# ------------------------------------------------- Q: persisted-run evaluation score


def test_persisted_run_evaluation_carries_score(app_client, db_ready):
    """(Q) MODE A scores the stored output and reports the run id."""
    _require_db(db_ready)
    created = app_client.post(
        "/api/prompts",
        json={"title": f"3K scoring {uuid.uuid4().hex[:8]}", "idea": "Score a persisted run."},
    )
    assert created.status_code == 201, created.text
    prompt_id = created.json()["id"]

    tested = app_client.post(
        "/api/testing/run",
        json={"prompt": "Write a welcome message", "prompt_id": prompt_id},
    )
    assert tested.status_code == 200, tested.text
    run_id = tested.json()["run_id"]
    assert run_id is not None

    response = app_client.post(
        "/api/evaluations/run",
        json={
            "run_id": run_id,
            "evaluator": {
                "rules": [
                    {"type": "contains", "text": "Simulated answer"},
                    {"type": "contains", "text": "Java"},
                ]
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == run_id
    assert body["passed"] is False
    assert body["score"] == 50.0


# ------------------------------------------------- R: supplied evaluation path


def test_supplied_comparison_exposes_factual_scores(app_client):
    """(R) Comparison MODE B derives scores from the supplied verdicts — one definition."""
    left = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {"prompt": "Write a welcome message"},
            "evaluator": {
                "rules": [
                    {"type": "contains", "text": "Simulated answer"},
                    {"type": "contains", "text": "Java"},
                ]
            },
        },
    ).json()
    right = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {"prompt": "Write a farewell"},
            "evaluator": {
                "rules": [
                    {"type": "contains", "text": "Simulated answer"},
                    {"type": "min_length", "length": 5},
                ]
            },
        },
    ).json()

    response = app_client.post(
        "/api/comparisons/run",
        json={"left_evaluation": left, "right_evaluation": right},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["left_execution"]["score"] == 50.0
    assert body["right_execution"]["score"] == 100.0
    assert body["metadata_diff"]["score_delta"] == 50.0
    for forbidden in ("winner", "better", "ranking", "recommendation"):
        assert forbidden not in response.text.lower()


def test_supplied_score_is_recomputed_from_verdicts(app_client):
    """(R) A stale client-supplied score cannot survive: it is re-derived."""
    left = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {"prompt": "Write a welcome message"},
            "evaluator": {"rules": [{"type": "contains", "text": "Simulated answer"}]},
        },
    ).json()
    left["score"] = 0.0  # tampered: disagrees with its own passing verdict
    right = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {"prompt": "Write a farewell"},
            "evaluator": {"rules": [{"type": "contains", "text": "Simulated answer"}]},
        },
    ).json()

    response = app_client.post(
        "/api/comparisons/run",
        json={"left_evaluation": left, "right_evaluation": right},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["left_execution"]["score"] == 100.0
    assert body["metadata_diff"]["score_delta"] == 0.0


# ------------------------------------------------- S: foreign run stays 404


def test_unknown_run_evaluation_is_404_without_score(app_client):
    """(S) Unknown runs fail exactly as before; no score side-channel exists."""
    response = app_client.post(
        "/api/evaluations/run",
        json={
            "run_id": str(uuid.uuid4()),
            "evaluator": {"rules": [{"type": "contains", "text": "x"}]},
        },
    )
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Prompt run not found"}
    assert "score" not in response.text


# ------------------------------------------------- T/U/V: suite + experiment carry scores


def test_suite_evaluations_carry_scores(app_client):
    """(T)(U) 3G aggregation stays total/passed; each per-target result has a score."""
    response = app_client.post(
        "/api/evaluations/suite",
        json={
            "evaluator": {
                "rules": [
                    {"type": "contains", "text": "Simulated answer"},
                    {"type": "contains", "text": "Java"},
                ]
            },
            "targets": [
                {"execution": {"prompt": "Write a welcome message"}},
                {"execution": {"prompt": "Write a farewell"}},
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert body["passed"] == 0
    assert [item["score"] for item in body["evaluations"]] == [50.0, 50.0]
    assert "average" not in response.text.lower()


def test_experiment_evaluations_carry_scores(app_client, db_ready):
    """(V) 3H counts stay total_versions/passed; per-version results carry scores."""
    _require_db(db_ready)
    created = app_client.post(
        "/api/prompts",
        json={
            "title": f"3K experiment {uuid.uuid4().hex[:8]}",
            "idea": "Scores on experiment versions.",
            "body": "Write a welcome message",
        },
    )
    assert created.status_code == 201, created.text
    prompt_id = created.json()["id"]
    app_client.put(f"/api/prompts/{prompt_id}", json={"body": "unrelated body"})

    response = app_client.post(
        "/api/experiments/run",
        json={
            "prompt_id": prompt_id,
            "evaluator": {
                "rules": [
                    {"type": "contains", "text": "Simulated answer"},
                    {"type": "contains", "text": "welcome"},
                ]
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_versions"] == 2
    assert body["passed"] == 1
    assert [item["score"] for item in body["evaluations"]] == [100.0, 50.0]
    for forbidden in ("winner", "best", "ranking", "average"):
        assert forbidden not in response.text.lower()


# ------------------------------------------------- W/X: 3I + 3J untouched


def test_version_create_and_restore_flows_unchanged(app_client, db_ready):
    """(W)(X) 3I append + 3J history/restore behave exactly as before (now with scores elsewhere)."""
    _require_db(db_ready)
    created = app_client.post(
        "/api/prompts",
        json={
            "title": f"3K versions {uuid.uuid4().hex[:8]}",
            "idea": "Scoring must not touch versioning.",
            "body": "Write a short email",
        },
    )
    assert created.status_code == 201, created.text
    prompt_id = created.json()["id"]
    assert created.json()["version_number"] == 1

    updated = app_client.put(f"/api/prompts/{prompt_id}", json={"body": "Write a long email"})
    assert updated.json()["version_number"] == 2

    history = app_client.get(f"/api/prompts/{prompt_id}/versions").json()
    assert [item["version_number"] for item in history] == [1, 2]
    v1_id = next(item["id"] for item in history if item["version_number"] == 1)

    restored = app_client.post(f"/api/prompts/{prompt_id}/versions/{v1_id}/restore")
    assert restored.status_code == 201, restored.text
    assert restored.json()["version_number"] == 3
    assert restored.json()["body"] == "Write a short email"


def test_no_schema_change_from_scoring(db_ready):
    """(Step 22) Scoring adds no tables, columns, or migrations."""
    _require_db(db_ready)
    from sqlalchemy import inspect as sa_inspect

    from app.core.database import Base
    from app.models import PromptRun, PromptVersion

    assert "score" not in PromptRun.__table__.columns
    assert "score" not in PromptVersion.__table__.columns
    # 4B: alembic_version is migration bookkeeping, not an app table (same
    # exclusion as test_migrations.py); any other extra table still fails here.
    assert set(sa_inspect(engine).get_table_names()) - {"alembic_version"} == set(
        Base.metadata.tables.keys()
    )
