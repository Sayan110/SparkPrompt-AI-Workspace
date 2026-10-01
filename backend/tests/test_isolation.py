"""Phase 4A cross-user isolation: two real signed-up accounts, one boundary.

Built through the real signup flow (not fixture rows), this proves the
non-leaking 404 rule end to end: for user B every read or write against user
A's resource answers byte-identically to an id that never existed, so even
existence cannot be probed. Lists contain only the caller's own rows, run and
evaluation records are ownership-resolved before any AI call, and the service
layer fails closed when invoked with no identity at all. Conventions match the
other integration modules: ``integration`` marker, module-scoped ``db_ready``,
and ``PlainTestClient`` for explicitly signed-up sessions.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal, engine
from app.models import Project
from app.services.demo_user import get_or_create_demo_user
from app.services.errors import NotAuthenticatedError

from conftest import PlainTestClient

pytestmark = pytest.mark.integration

PASSWORD = "4a-Isolation-Passw0rd"
_UUID_IN_URL = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


# ----------------------------------------------------------------- db helpers


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


# ------------------------------------------------------------------- helpers


def _client() -> PlainTestClient:
    from app.main import app

    return PlainTestClient(app)


def _signup_client(prefix: str) -> tuple[PlainTestClient, dict]:
    """A fresh client signed up as its own new account (real signup endpoint)."""
    client = _client()
    email = f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"
    response = client.post(
        "/api/auth/signup", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    return client, response.json()


def _unknown_url(url: str) -> str:
    """Same route with every UUID replaced by ids that exist nowhere."""
    return _UUID_IN_URL.sub(lambda _match: str(uuid.uuid4()), url)


def _assert_non_leaking_404(
    client,
    method: str,
    url: str,
    *,
    body: dict | None = None,
    unknown_body: dict | None = None,
) -> None:
    """A foreign id and a never-existed id must answer byte-identical 404s.

    The real URL carries user A's ids; the unknown counterpart rewrites every
    UUID in the URL (or uses ``unknown_body`` when the ids ride in the payload).
    Identical status AND body is what makes existence unprobeable.
    """
    if body is not None:
        foreign_kwargs = {"json": body}
        missing_kwargs = {"json": unknown_body if unknown_body is not None else body}
    else:
        foreign_kwargs = {}
        missing_kwargs = {}

    foreign = client.request(method, url, **foreign_kwargs)
    missing = client.request(method, _unknown_url(url), **missing_kwargs)
    assert foreign.status_code == 404, (
        f"{method} {url} -> {foreign.status_code}: {foreign.text[:300]}"
    )
    assert missing.status_code == 404, (
        f"{method} (never-existed id) -> {missing.status_code}: {missing.text[:300]}"
    )
    assert foreign.json() == missing.json(), (
        f"404 differs between foreign and unknown id for {method} {url}: "
        f"{foreign.json()!r} != {missing.json()!r}"
    )


def _evaluator() -> dict:
    return {"name": "qa check", "rules": [{"type": "contains", "text": "Simulated answer"}]}


# --------------------------------------------------------------------- tests


def test_new_account_starts_empty_and_cannot_see_the_demo_workspace(db_ready):
    """A signup owns exactly its default project and zero of demo's rows."""
    _require_db(db_ready)
    client, user = _signup_client("4a-empty")
    assert user["email"].startswith("4a-empty-")

    projects = client.get("/api/projects")
    assert projects.status_code == 200, projects.text
    assert len(projects.json()) == 1, "exactly the account's own default project"
    # The demo workspace holds thousands of prompts; a new signup sees none.
    assert client.get("/api/prompts").json() == []

    with SessionLocal() as session:
        demo = get_or_create_demo_user(session)
        demo_project_ids = {
            str(row.id)
            for row in session.scalars(
                select(Project).where(Project.user_id == demo.id)
            ).all()
        }
    own_ids = {row["id"] for row in projects.json()}
    assert own_ids.isdisjoint(demo_project_ids)


def test_projects_prompts_and_versions_are_isolated_with_identical_404s(db_ready):
    """Every verb against A's rows from B is the same 404 an unknown id gives."""
    _require_db(db_ready)
    a, _ = _signup_client("4a-iso-a")
    b, _ = _signup_client("4a-iso-b")

    project = a.post("/api/projects", json={"name": f"Private {uuid.uuid4().hex[:6]}"})
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    prompt = a.post(
        "/api/prompts",
        json={
            "title": "A private prompt",
            "idea": "hidden from B",
            "body": "version one",
            "project_id": project_id,
        },
    )
    assert prompt.status_code == 201, prompt.text
    prompt_id = prompt.json()["id"]

    updated = a.put(f"/api/prompts/{prompt_id}", json={"body": "version two"})
    assert updated.status_code == 200, updated.text
    versions = a.get(f"/api/prompts/{prompt_id}/versions")
    assert versions.status_code == 200, versions.text
    assert [row["version_number"] for row in versions.json()] == [1, 2]
    version_id = versions.json()[0]["id"]

    # B probing A's resources — reads, writes, deletes, sub-resources.
    _assert_non_leaking_404(b, "GET", f"/api/projects/{project_id}")
    _assert_non_leaking_404(b, "PUT", f"/api/projects/{project_id}", body={"name": "stolen"})
    _assert_non_leaking_404(b, "DELETE", f"/api/projects/{project_id}")
    _assert_non_leaking_404(b, "GET", f"/api/prompts/{prompt_id}")
    _assert_non_leaking_404(b, "PUT", f"/api/prompts/{prompt_id}", body={"body": "stolen"})
    _assert_non_leaking_404(b, "DELETE", f"/api/prompts/{prompt_id}")
    _assert_non_leaking_404(b, "GET", f"/api/prompts/{prompt_id}/versions")
    _assert_non_leaking_404(
        b, "POST", f"/api/prompts/{prompt_id}/versions/{version_id}/restore"
    )

    # B cannot write INTO A's project either: the container is owned too.
    stolen = b.post(
        "/api/prompts",
        json={"title": "into A's project", "idea": "x", "project_id": project_id},
    )
    ghost = b.post(
        "/api/prompts",
        json={
            "title": "into a ghost project",
            "idea": "x",
            "project_id": str(uuid.uuid4()),
        },
    )
    assert stolen.status_code == 404, stolen.text
    assert ghost.status_code == 404, ghost.text
    assert stolen.json() == ghost.json()

    # A's workspace survived every probe intact.
    assert a.get(f"/api/projects/{project_id}").status_code == 200
    assert a.get(f"/api/prompts/{prompt_id}").status_code == 200
    assert len(a.get(f"/api/prompts/{prompt_id}/versions").json()) == 2
    assert all(row["id"] != prompt_id for row in b.get("/api/prompts").json())


def test_lists_contain_only_the_callers_own_rows(db_ready):
    """Neither direction leaks: A's and B's lists are disjoint by construction."""
    _require_db(db_ready)
    a, _ = _signup_client("4a-list-a")
    b, _ = _signup_client("4a-list-b")

    a_project = a.post("/api/projects", json={"name": f"A list {uuid.uuid4().hex[:6]}"})
    assert a_project.status_code == 201, a_project.text
    a_prompt = a.post("/api/prompts", json={"title": "A list prompt", "idea": "mine"})
    assert a_prompt.status_code == 201, a_prompt.text
    b_prompt = b.post("/api/prompts", json={"title": "B list prompt", "idea": "mine"})
    assert b_prompt.status_code == 201, b_prompt.text

    a_projects = a.get("/api/projects").json()
    b_projects = b.get("/api/projects").json()
    a_prompts = a.get("/api/prompts").json()
    b_prompts = b.get("/api/prompts").json()

    # A: signup default + the one it created. B: its own default only.
    assert len(a_projects) == 2
    assert len(b_projects) == 1
    a_project_ids = {row["id"] for row in a_projects}
    assert a_project.json()["id"] in a_project_ids
    assert a_project_ids.isdisjoint({row["id"] for row in b_projects})

    a_prompt_ids = {row["id"] for row in a_prompts}
    b_prompt_ids = {row["id"] for row in b_prompts}
    assert len(a_prompts) == 1 and len(b_prompts) == 1
    assert a_prompt.json()["id"] in a_prompt_ids
    assert b_prompt.json()["id"] in b_prompt_ids
    assert a_prompt_ids.isdisjoint(b_prompt_ids)


def test_runs_evaluations_and_comparisons_are_owned_before_any_ai_call(
    db_ready, registry, monkeypatch
):
    """Run/evaluation records resolve ownership first: foreign ids are unrunnable."""
    _require_db(db_ready)
    from app.api.routes import ai as ai_routes

    monkeypatch.setattr(ai_routes, "_REGISTRY_CACHE", registry)

    a, _ = _signup_client("4a-run-a")
    b, _ = _signup_client("4a-run-b")

    prompt = a.post(
        "/api/prompts",
        json={
            "title": "A run prompt",
            "idea": "hidden",
            "body": "Write a welcome message",
        },
    )
    assert prompt.status_code == 201, prompt.text
    prompt_id = prompt.json()["id"]

    run = a.post(
        "/api/testing/run",
        json={
            "prompt": "Write a welcome message",
            "provider": "fake",
            "model": "fake-model-1",
            "prompt_id": prompt_id,
        },
    )
    assert run.status_code == 200, run.text
    run_id = run.json()["run_id"]

    evaluation = a.post(
        "/api/evaluations/run", json={"evaluator": _evaluator(), "run_id": run_id}
    )
    assert evaluation.status_code == 200, evaluation.text
    evaluation_id = evaluation.json()["evaluation_id"]

    # A reads its own records.
    assert a.get(f"/api/evaluations/{evaluation_id}").status_code == 200
    history = a.get(f"/api/prompts/{prompt_id}/evaluations")
    assert history.status_code == 200, history.text
    assert {row["evaluation_id"] for row in history.json()} == {evaluation_id}

    # B probing A's records by URL id.
    _assert_non_leaking_404(b, "GET", f"/api/evaluations/{evaluation_id}")
    _assert_non_leaking_404(b, "GET", f"/api/prompts/{prompt_id}/evaluations")

    # B cannot execute A's prompt (cross-tenant write refused before the AI call).
    _assert_non_leaking_404(
        b,
        "POST",
        "/api/testing/run",
        body={
            "prompt": "Write a welcome message",
            "provider": "fake",
            "model": "fake-model-1",
            "prompt_id": prompt_id,
        },
        unknown_body={
            "prompt": "Write a welcome message",
            "provider": "fake",
            "model": "fake-model-1",
            "prompt_id": str(uuid.uuid4()),
        },
    )

    # B cannot evaluate A's run.
    _assert_non_leaking_404(
        b,
        "POST",
        "/api/evaluations/run",
        body={"evaluator": _evaluator(), "run_id": run_id},
        unknown_body={"evaluator": _evaluator(), "run_id": str(uuid.uuid4())},
    )

    # B cannot compare against A's runs.
    _assert_non_leaking_404(
        b,
        "POST",
        "/api/comparisons/run",
        body={"left_run_id": run_id, "right_run_id": run_id, "evaluator": _evaluator()},
        unknown_body={
            "left_run_id": str(uuid.uuid4()),
            "right_run_id": str(uuid.uuid4()),
            "evaluator": _evaluator(),
        },
    )

    # A's own flows still work after all of B's attempts.
    again = a.post(
        "/api/testing/run",
        json={
            "prompt": "Write a welcome message",
            "provider": "fake",
            "model": "fake-model-1",
            "prompt_id": prompt_id,
        },
    )
    assert again.status_code == 200, again.text


def test_service_layer_fails_closed_without_identity(db_ready):
    """A direct service call with no stamped identity refuses to touch any data."""
    _require_db(db_ready)
    from app.services import prompts as prompts_service
    from app.services import projects as projects_service

    with SessionLocal() as session:
        with pytest.raises(NotAuthenticatedError, match="Authentication required"):
            projects_service.list_projects(session)
    with SessionLocal() as session:
        with pytest.raises(NotAuthenticatedError, match="Authentication required"):
            prompts_service.list_prompts(session)
