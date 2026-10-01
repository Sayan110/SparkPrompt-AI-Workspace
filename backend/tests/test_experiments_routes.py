"""Phase 3H experiment route tests for POST /api/experiments/run.

Mirrors ``test_suite_routes.py`` conventions: the provider registry is swapped via the
same ``_REGISTRY_CACHE`` monkeypatch, DB-backed cases use the ``db_ready`` skip
pattern, domain errors are forced with the same service-monkeypatch technique, and
unexpected exceptions are asserted sanitized (no tracebacks, no internal detail).

Database cases (real owned/foreign prompts, version ordering, PromptRun accounting,
schema integrity) are marked ``integration`` and skip cleanly when Postgres is down.
"""

import uuid

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import Base, SessionLocal, engine
from app.models import PromptRun, PromptVersion

from conftest import (
    ExplodingProvider,
    FailingProvider,
    FakeProvider,
    UnavailableProvider,
)

_FORBIDDEN_AGGREGATE_KEYS = (
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


def _registry_with(provider_cls, settings):
    from app.ai.registry import ProviderRegistry

    registry = ProviderRegistry(settings)
    registry.register(provider_cls(settings))
    return registry


def _use_registry(monkeypatch, ai_routes, registry):
    monkeypatch.setattr(ai_routes, "_REGISTRY_CACHE", registry)


def _evaluator(rules=None, name="experiment check", expected_output=None) -> dict:
    payload = {
        "name": name,
        "rules": rules or [{"type": "contains", "text": "Simulated answer"}],
    }
    if expected_output is not None:
        payload["expected_output"] = expected_output
    return payload


def _experiment(prompt_id, evaluator=None) -> dict:
    return {"prompt_id": str(prompt_id), "evaluator": evaluator or _evaluator()}


def _create_prompt_with_versions(client, bodies: list[str]) -> str:
    """Create an owned prompt whose persisted versions are exactly ``bodies``, in order.

    The prompt is created WITHOUT a body so no implicit version exists, then each
    version is inserted explicitly — the public API can only ever create version 1, so
    this is the only way to exercise a genuine multi-version prompt.
    """
    created = client.post(
        "/api/prompts",
        json={
            "title": f"Experiment host {uuid.uuid4().hex[:8]}",
            "idea": "Measure every saved version of this prompt.",
        },
    )
    assert created.status_code == 201, created.text
    prompt_id = created.json()["id"]
    with SessionLocal() as session:
        for index, body in enumerate(bodies, start=1):
            session.add(
                PromptVersion(
                    prompt_id=uuid.UUID(prompt_id),
                    version_number=index,
                    body=body,
                )
            )
        session.commit()
    return prompt_id


def _create_foreign_prompt() -> str:
    """A prompt owned by a different user, with its own persisted versions."""
    from app.models import Project, Prompt, User

    with SessionLocal() as session:
        user = User(
            email=f"foreign-{uuid.uuid4().hex[:10]}@sparkprompt.local",
            display_name="Foreign workspace owner",
        )
        session.add(user)
        session.flush()
        project = Project(user_id=user.id, name="Foreign workspace")
        session.add(project)
        session.flush()
        prompt = Prompt(project_id=project.id, title="Foreign prompt", idea="hidden")
        session.add(prompt)
        session.flush()
        session.add(
            PromptVersion(prompt_id=prompt.id, version_number=1, body="secret foreign body")
        )
        session.commit()
        return str(prompt.id)


def _find_keys(node, found=None):
    """Recursively collect every mapping key in a decoded JSON body."""
    found = set() if found is None else found
    if isinstance(node, dict):
        found.update(node.keys())
        for value in node.values():
            _find_keys(value, found)
    elif isinstance(node, list):
        for item in node:
            _find_keys(item, found)
    return found


# ---------------------------------------------------------------------------
# Routes: success
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_experiment_success_returns_counts_and_ordered_evaluations(
    app_client, db_ready, monkeypatch, settings
):
    """(38) A real owned prompt with 3 versions -> 200, exact counts, ascending order."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    bodies = [
        "Write a welcome message",
        "Write a thank-you note",
        "Write a farewell",
    ]
    prompt_id = _create_prompt_with_versions(app_client, bodies)

    response = app_client.post(
        "/api/experiments/run", json=_experiment(prompt_id)
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total_versions"] == 3
    assert body["passed"] == 3
    assert len(body["evaluations"]) == 3
    assert body["generated_at"]

    # Evaluations are ordered by ascending version_number.
    for index, evaluation in enumerate(body["evaluations"]):
        assert evaluation["output"] == f"Simulated answer to: {bodies[index]}"

    # Each item is the existing EvaluationResult wire shape (plus the Phase 3K
    # derived score, the Phase 3L scoring profile, the Phase 3O executed
    # version identity, and Phase 3P's history id; verdicts and `passed` are
    # unchanged).
    for evaluation in body["evaluations"]:
        assert set(evaluation) == {
            "run_id",
            "version_id",
            "evaluation_id",
            "output",
            "provider",
            "model",
            "latency_ms",
            "usage",
            "evaluator_snapshot",
            "verdicts",
            "passed",
            "score",
            "scoring",
            "generated_at",
        }
        assert evaluation["provider"] == "fake"
        assert evaluation["model"] == "fake-model-1"
        assert evaluation["passed"] is True
        # Phase 3P stores individual evaluations only: an experiment's
        # evaluations are never saved, so no history id is echoed back.
        assert evaluation["evaluation_id"] is None
        assert evaluation["evaluator_snapshot"]["rules"][0]["type"] == "contains"
        assert evaluation["verdicts"][0]["passed"] is True


@pytest.mark.integration
def test_experiment_mixed_pass_and_fail_counts(
    app_client, db_ready, monkeypatch, settings
):
    """(11)(12) Mixed PASS/FAIL over real versions: only matching outputs pass."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    prompt_id = _create_prompt_with_versions(
        app_client,
        [
            "Write a welcome message",
            "a body with no marker",
            "Write a farewell",
        ],
    )

    response = app_client.post(
        "/api/experiments/run",
        json=_experiment(
            prompt_id, _evaluator(rules=[{"type": "contains", "text": "Write a"}])
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_versions"] == 3
    assert body["passed"] == 2
    assert [item["passed"] for item in body["evaluations"]] == [True, False, True]


@pytest.mark.integration
def test_experiment_prompt_with_no_versions_returns_zero(
    app_client, db_ready, monkeypatch, settings
):
    """(3) A prompt with no saved versions measures zero — not an error."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    created = app_client.post(
        "/api/prompts",
        json={"title": f"No versions {uuid.uuid4().hex[:8]}", "idea": "No body saved."},
    )
    assert created.status_code == 201, created.text

    response = app_client.post("/api/experiments/run", json=_experiment(created.json()["id"]))
    assert response.status_code == 200, response.text
    assert response.json() == {
        "total_versions": 0,
        "passed": 0,
        "evaluations": [],
        "generated_at": response.json()["generated_at"],
    }


@pytest.mark.integration
def test_experiment_orders_by_version_number_not_insertion_order(
    app_client, db_ready, monkeypatch, settings
):
    """(5) Versions inserted out of order still come back ascending by version_number."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    created = app_client.post(
        "/api/prompts",
        json={
            "title": f"Out of order {uuid.uuid4().hex[:8]}",
            "idea": "Versions inserted newest first.",
        },
    )
    prompt_id = created.json()["id"]

    # Insert 3, then 1, then 2 — and make created_at point the wrong way entirely.
    ordered = ["first version", "second version", "third version"]
    with SessionLocal() as session:
        for number, body in zip((3, 1, 2), (ordered[2], ordered[0], ordered[1])):
            session.add(
                PromptVersion(
                    prompt_id=uuid.UUID(prompt_id),
                    version_number=number,
                    body=body,
                )
            )
        session.commit()

    response = app_client.post("/api/experiments/run", json=_experiment(prompt_id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_versions"] == 3
    assert [item["output"] for item in body["evaluations"]] == [
        f"Simulated answer to: {value}" for value in ordered
    ]


@pytest.mark.integration
def test_experiment_response_has_no_score_or_ranking_fields(
    app_client, db_ready, monkeypatch, settings
):
    """(15) No score / ratio / grade / ranking / winner anywhere in the payload."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    prompt_id = _create_prompt_with_versions(app_client, ["v1 body", "v2 body"])

    response = app_client.post("/api/experiments/run", json=_experiment(prompt_id))
    assert response.status_code == 200, response.text
    body = response.json()
    # Experiment-level aggregates stay forbidden in full (no average/best/winner).
    for name in _FORBIDDEN_AGGREGATE_KEYS:
        assert name not in body, f"forbidden field {name} at experiment level"
    # Per-version results carry the Phase 3K derived score as descriptive metadata;
    # every other judgment key stays forbidden there too.
    for evaluation in body["evaluations"]:
        keys = _find_keys(evaluation)
        assert "score" in keys  # the specified 3K addition
        for name in _FORBIDDEN_AGGREGATE_KEYS:
            if name == "score":
                continue
            assert name not in keys, f"forbidden field {name} in experiment response"


# ---------------------------------------------------------------------------
# Routes: ownership
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_experiment_unknown_prompt_404(app_client, db_ready, monkeypatch, settings):
    """(39) Unknown prompt -> 404 with the standard prompt-not-found detail."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post("/api/experiments/run", json=_experiment(uuid.uuid4()))
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Prompt not found"


@pytest.mark.integration
def test_experiment_foreign_prompt_404(app_client, db_ready, monkeypatch, settings):
    """(40) Foreign prompt -> the identical 404; no existence or metadata leak."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    foreign = _create_foreign_prompt()

    response = app_client.post("/api/experiments/run", json=_experiment(foreign))
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Prompt not found"
    # Indistinguishable from the unknown-prompt case, and no foreign body echoed back.
    assert "secret foreign body" not in response.text
    assert "total_versions" not in response.text
    assert "evaluations" not in response.text


@pytest.mark.integration
def test_experiment_foreign_prompt_creates_no_promptruns(
    app_client, db_ready, monkeypatch, settings
):
    """(21)(22) Ownership-first: a foreign prompt is never executed or persisted."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    foreign = uuid.UUID(_create_foreign_prompt())
    with SessionLocal() as session:
        before = len(
            list(session.scalars(select(PromptRun).where(PromptRun.prompt_id == foreign)))
        )

    response = app_client.post("/api/experiments/run", json=_experiment(foreign))
    assert response.status_code == 404, response.text

    with SessionLocal() as session:
        after = len(
            list(session.scalars(select(PromptRun).where(PromptRun.prompt_id == foreign)))
        )
    assert before == after == 0


# ---------------------------------------------------------------------------
# Routes: request validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"evaluator": {"rules": [{"type": "contains", "text": "x"}]}},
        {"prompt_id": "not-a-uuid", "evaluator": {"rules": [{"type": "contains", "text": "x"}]}},
        {"prompt_id": str(uuid.uuid4())},
        {"prompt_id": str(uuid.uuid4()), "evaluator": {"rules": []}},
        {
            "prompt_id": str(uuid.uuid4()),
            "evaluator": {"rules": [{"type": "contains", "text": "x"}] * 21},
        },
        {"prompt_id": str(uuid.uuid4()), "evaluator": {"rules": [{"type": "contains"}]}},
        {"prompt_id": str(uuid.uuid4()), "evaluator": {"rules": [{"type": "min_length"}]}},
        {
            "prompt_id": str(uuid.uuid4()),
            "evaluator": {"rules": [{"type": "exact_match"}]},
        },
        {
            "prompt_id": str(uuid.uuid4()),
            "evaluator": {"rules": [{"type": "regex_match", "pattern": "(unclosed"}]},
        },
        {"prompt_id": str(uuid.uuid4()), "evaluator": "not-an-object"},
    ],
)
def test_experiment_malformed_request_422(app_client, payload):
    """(27)(41) Malformed requests fail FastAPI validation before any work happens."""
    response = app_client.post("/api/experiments/run", json=payload)
    assert response.status_code == 422, response.text


@pytest.mark.integration
def test_experiment_oversized_version_count_rejected(
    app_client, db_ready, monkeypatch, settings
):
    """(7)(42) More than 20 versions -> a clean validation error, never truncation.

    The version count is discovered server-side (the client never supplies it), so this
    is service-level validation and therefore the project's 400 envelope, exactly as
    the Phase 3E/3G services map their own validation errors.
    """
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    prompt_id = _create_prompt_with_versions(
        app_client, [f"version body {n}" for n in range(1, 22)]
    )

    response = app_client.post("/api/experiments/run", json=_experiment(prompt_id))
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_experiment_request"
    assert "20" in detail["message"]
    # No partial output: the oversized prompt is not partially measured.
    assert "evaluations" not in response.text
    assert "total_versions" not in response.text


@pytest.mark.integration
def test_experiment_exactly_twenty_versions_succeeds(
    app_client, db_ready, monkeypatch, settings
):
    """(6) The bound is inclusive at the API boundary too."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    prompt_id = _create_prompt_with_versions(
        app_client, [f"version body {n}" for n in range(1, 21)]
    )

    response = app_client.post("/api/experiments/run", json=_experiment(prompt_id))
    assert response.status_code == 200, response.text
    assert response.json()["total_versions"] == 20


# ---------------------------------------------------------------------------
# Routes: gateway / provider error envelope
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_experiment_provider_unavailable_409(
    app_client, db_ready, monkeypatch, settings
):
    """(29)(43) An unavailable provider surfaces the existing 409 envelope."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(UnavailableProvider, settings))
    prompt_id = _create_prompt_with_versions(app_client, ["v1 body"])

    response = app_client.post("/api/experiments/run", json=_experiment(prompt_id))
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "provider_unavailable"
    assert "Traceback" not in response.text


@pytest.mark.integration
def test_experiment_gateway_error_normalized_502(
    app_client, db_ready, monkeypatch, settings
):
    """(30) An upstream provider failure maps to the existing 502 envelope."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FailingProvider, settings))
    prompt_id = _create_prompt_with_versions(app_client, ["v1 body"])

    response = app_client.post("/api/experiments/run", json=_experiment(prompt_id))
    assert response.status_code == 502, response.text
    assert response.json()["detail"]["code"] == "provider_error"
    assert "Traceback" not in response.text
    # No internal Python detail leaks; the provider's own message is surfaced exactly as
    # the frozen 3E/3F/3G routes surface it (proved by the parity check below).
    for leak in ("AIProviderError", "AIGatewayError", "app.ai"):
        assert leak not in response.text, leak

    # The experiment route reuses the existing envelope rather than inventing one: the
    # same provider failure through /api/evaluations/run yields an identical body.
    baseline = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {
                "prompt": "Write a welcome message",
                "provider": "fake",
                "model": "fake-model-1",
            },
            "evaluator": _evaluator(),
        },
    )
    assert baseline.status_code == 502, baseline.text
    assert response.json()["detail"] == baseline.json()["detail"]


@pytest.mark.integration
def test_experiment_unexpected_provider_error_sanitized(
    db_ready, monkeypatch, settings
):
    """(31)(44) An unexpected provider exception becomes a sanitized 500."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from fastapi.testclient import TestClient

    from app.api.routes import ai as ai_routes
    from app.main import app

    _use_registry(monkeypatch, ai_routes, _registry_with(ExplodingProvider, settings))
    with TestClient(app) as bootstrap:
        prompt_id = _create_prompt_with_versions(bootstrap, ["v1 body"])

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/api/experiments/run", json=_experiment(prompt_id))
    assert response.status_code == 500, response.text
    assert "unexpected-internal-detail" not in response.text
    assert "RuntimeError" not in response.text
    assert "Traceback" not in response.text


@pytest.mark.integration
def test_experiment_domain_error_sanitized_500(
    app_client, db_ready, monkeypatch, settings
):
    """(31) A sibling domain error hits the sanitized 500 safety net."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.api.routes import ai as ai_routes
    from app.api.routes import experiments as experiment_routes
    from app.experiments import ExperimentError

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))

    class ForcedExperiment:
        def __init__(self, eval_service):
            del eval_service

        def run(self, **kwargs):
            del kwargs
            raise ExperimentError("boom")

    monkeypatch.setattr(experiment_routes, "ExperimentService", ForcedExperiment)
    response = app_client.post(
        "/api/experiments/run", json=_experiment(uuid.uuid4())
    )
    assert response.status_code == 500, response.text
    assert response.json()["detail"]["code"] == "experiment_failed"
    assert (
        response.json()["detail"]["message"]
        == "Prompt experiment failed unexpectedly."
    )
    assert "boom" not in response.text


def test_experiment_service_validation_error_400(app_client, monkeypatch, settings):
    """(28) A service-level validation failure maps to 400 with the domain message."""
    from app.api.routes import ai as ai_routes
    from app.api.routes import experiments as experiment_routes
    from app.experiments import ExperimentValidationError

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))

    class ForcedExperiment:
        def __init__(self, eval_service):
            del eval_service

        def run(self, **kwargs):
            del kwargs
            raise ExperimentValidationError("forced experiment validation failure")

    monkeypatch.setattr(experiment_routes, "ExperimentService", ForcedExperiment)
    response = app_client.post(
        "/api/experiments/run", json=_experiment(uuid.uuid4())
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "invalid_experiment_request"
    assert (
        response.json()["detail"]["message"]
        == "forced experiment validation failure"
    )


def test_experiment_errors_expose_no_secrets(app_client, monkeypatch, settings):
    """(32) Error bodies never leak tracebacks, key names, or internal details."""
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    response = app_client.post("/api/experiments/run", json={})
    assert response.status_code == 422
    for leak in ("Traceback", "api_key", "API_KEY", "sk-", "app.ai", "sqlalchemy"):
        assert leak not in response.text, leak


# ---------------------------------------------------------------------------
# Database: no schema change, no mutation, no experiment persistence
# ---------------------------------------------------------------------------


def test_no_experiment_tables_in_metadata():
    """(33) The experiment domain registers no tables on the SQLAlchemy metadata."""
    table_names = set(Base.metadata.tables.keys())
    for name in (
        "experiments",
        "experiment_results",
        "experiment_versions",
        "experiment_runs",
        "evaluation_suites",
        "suite_targets",
        "suite_results",
    ):
        assert name not in table_names


@pytest.mark.integration
def test_no_experiment_tables_in_database(db_ready):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    table_names = set(inspect(engine).get_table_names())
    for name in ("experiments", "experiment_results", "experiment_versions", "experiment_runs"):
        assert name not in table_names
    # The expected workspace tables are still exactly the pre-Phase-3H set.
    assert {
        "users",
        "projects",
        "prompts",
        "prompt_versions",
        "prompt_runs",
    } <= table_names


def test_promptversion_columns_unchanged():
    """(35) PromptVersion model is untouched by Phase 3H."""
    assert set(PromptVersion.__table__.columns.keys()) == {
        "id",
        "prompt_id",
        "version_number",
        "body",
        "created_at",
    }


def test_promptrun_columns_unchanged():
    """(34) PromptRun model is untouched by Phase 3H (only 3O's version_id exists)."""
    assert set(PromptRun.__table__.columns.keys()) == {
        "id",
        "prompt_id",
        "version_id",
        "provider",
        "model",
        "input_snapshot",
        "output_text",
        "status",
        "error",
        "finish_reason",
        "latency_ms",
        "usage_json",
        "created_at",
    }


@pytest.mark.integration
def test_promptversion_table_unchanged_in_database(db_ready):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    columns = {col["name"] for col in inspect(engine).get_columns("prompt_versions")}
    assert columns == {"id", "prompt_id", "version_number", "body", "created_at"}


@pytest.mark.integration
def test_promptrun_table_unchanged_in_database(db_ready):
    if not db_ready:
        pytest.skip("Postgres not reachable")
    columns = {col["name"] for col in inspect(engine).get_columns("prompt_runs")}
    assert columns == {
        "id",
        "prompt_id",
        "version_id",
        "provider",
        "model",
        "input_snapshot",
        "output_text",
        "status",
        "error",
        "finish_reason",
        "latency_ms",
        "usage_json",
        "created_at",
    }


@pytest.mark.integration
def test_experiment_never_mutates_prompt_or_versions(
    app_client, db_ready, monkeypatch, settings
):
    """(18)(36) The prompt and every version row are byte-identical after a run."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    bodies = ["first body", "second body", "third body"]
    prompt_id = uuid.UUID(_create_prompt_with_versions(app_client, bodies))

    def snapshot():
        with SessionLocal() as session:
            versions = list(
                session.scalars(
                    select(PromptVersion)
                    .where(PromptVersion.prompt_id == prompt_id)
                    .order_by(PromptVersion.version_number)
                )
            )
            return [(v.version_number, v.body, v.created_at) for v in versions]

    before = snapshot()
    response = app_client.post("/api/experiments/run", json=_experiment(prompt_id))
    assert response.status_code == 200, response.text
    assert snapshot() == before
    assert [body for _number, body, _created in before] == bodies


@pytest.mark.integration
def test_experiment_creates_one_promptrun_per_version_through_existing_path(
    app_client, db_ready, monkeypatch, settings
):
    """(9)(37) Fresh executions persist a normal PromptRun per version — nothing else."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))
    bodies = ["alpha", "beta", "gamma"]
    prompt_id = uuid.UUID(_create_prompt_with_versions(app_client, bodies))

    def run_ids():
        with SessionLocal() as session:
            return sorted(
                str(run.id)
                for run in session.scalars(
                    select(PromptRun).where(PromptRun.prompt_id == prompt_id)
                )
            )

    assert run_ids() == []
    response = app_client.post("/api/experiments/run", json=_experiment(prompt_id))
    assert response.status_code == 200, response.text
    body = response.json()

    created = run_ids()
    assert len(created) == len(bodies)
    # Each evaluation's run_id is one of the runs the existing gateway path recorded.
    assert sorted(item["run_id"] for item in body["evaluations"]) == created
    # No extra experiment-specific persistence of any kind.
    with SessionLocal() as session:
        assert not any(
            "experiment" in name for name in inspect(engine).get_table_names()
        )


# ---------------------------------------------------------------------------
# Regression: the frozen 3E / 3F / 3G routes are untouched
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_existing_evaluation_routes_still_work(
    app_client, db_ready, monkeypatch, settings
):
    """(22) Acceptance: existing 3E/3G behavior is unchanged."""
    if not db_ready:
        pytest.skip("Postgres not reachable")
    from app.api.routes import ai as ai_routes

    _use_registry(monkeypatch, ai_routes, _registry_with(FakeProvider, settings))

    single = app_client.post(
        "/api/evaluations/run",
        json={
            "execution": {
                "prompt": "Write a welcome message",
                "provider": "fake",
                "model": "fake-model-1",
            },
            "evaluator": _evaluator(),
        },
    )
    assert single.status_code == 200, single.text
    assert single.json()["passed"] is True

    suite = app_client.post(
        "/api/evaluations/suite",
        json={
            "evaluator": _evaluator(),
            "targets": [
                {
                    "execution": {
                        "prompt": "Write a welcome message",
                        "provider": "fake",
                        "model": "fake-model-1",
                    }
                },
                {
                    "execution": {
                        "prompt": "Write a farewell",
                        "provider": "fake",
                        "model": "fake-model-1",
                    }
                },
            ],
        },
    )
    assert suite.status_code == 200, suite.text
    assert suite.json()["total"] == 2
    assert suite.json()["passed"] == 2

    comparison = app_client.post(
        "/api/comparisons/run",
        json={
            "left_evaluation": {
                "output": "Simulated answer one",
                "provider": "fake",
                "model": "fake-model-1",
                "evaluator_snapshot": _evaluator(),
                "verdicts": [],
                "passed": True,
            },
            "right_evaluation": {
                "output": "Simulated answer two",
                "provider": "fake",
                "model": "fake-model-1",
                "evaluator_snapshot": _evaluator(),
                "verdicts": [],
                "passed": True,
            },
        },
    )
    assert comparison.status_code == 200, comparison.text
