"""Phase 3I prompt-versioning tests: PUT /api/prompts/{id} appends a PromptVersion.

Covers the Phase 3I acceptance matrix (A-O) and the STEP 13 checklist items. The
conventions are the ones already used by ``test_prompt_run_integration.py`` and
``test_experiments_routes.py``: the ``integration`` marker, a module-scoped
``db_ready`` reachability check that skips cleanly when Postgres is down, the conftest
``app_client`` fixture for the fake-provider client, and direct ``SessionLocal`` reads
whenever a claim is about what is actually stored rather than what the API returned.

Two claims are proved here rather than assumed:

* **Version ordering never consults ``created_at``.** ``test_version_ordering_ignores_created_at``
  stores versions whose insertion order deliberately contradicts their numbers *and* whose
  ``created_at`` values point at a different "latest", then asserts the API follows
  ``version_number``.
* **Concurrent body edits cannot append the same number twice.** The write path takes a
  ``SELECT ... FOR UPDATE`` on the prompt row, so two real threads racing on one prompt get
  distinct, sequential version numbers.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal, engine
from app.models import Prompt, PromptVersion
from app.services.identity import IDENTITY_KEY

from conftest import FakeProvider  # noqa: F401  (import proves the conftest contract holds)

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- helpers


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


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _create_prompt(client, body: str | None = None, **overrides) -> dict:
    """POST /api/prompts. A supplied ``body`` becomes version 1."""
    payload = {
        "title": overrides.pop("title", f"3I versioning {uuid.uuid4().hex[:8]}"),
        "idea": overrides.pop("idea", "Append new prompt versions without losing the old ones."),
    }
    payload.update(overrides)
    if body is not None:
        payload["body"] = body
    response = client.post("/api/prompts", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _versions(prompt_id: str) -> list[tuple[int, str]]:
    """Every stored version as ``(version_number, body)``, ascending by version_number."""
    with SessionLocal() as session:
        rows = session.execute(
            select(PromptVersion.version_number, PromptVersion.body)
            .where(PromptVersion.prompt_id == uuid.UUID(prompt_id))
            .order_by(PromptVersion.version_number.asc())
        ).all()
    return [(int(number), body) for number, body in rows]


def _version_snapshot(prompt_id: str) -> list[tuple[uuid.UUID, int, str, datetime]]:
    """Full stored rows, so "unchanged" can be asserted byte-for-byte."""
    with SessionLocal() as session:
        rows = session.execute(
            select(
                PromptVersion.id,
                PromptVersion.version_number,
                PromptVersion.body,
                PromptVersion.created_at,
            )
            .where(PromptVersion.prompt_id == uuid.UUID(prompt_id))
            .order_by(PromptVersion.version_number.asc())
        ).all()
    return [(rid, int(number), body, created) for rid, number, body, created in rows]


def _insert_versions(prompt_id: str, rows: list[tuple[int, str, datetime]]) -> None:
    """Seed versions directly, so ordering/edge cases the public API cannot reach are testable."""
    with SessionLocal() as session:
        for number, body, created_at in rows:
            session.add(
                PromptVersion(
                    prompt_id=uuid.UUID(prompt_id),
                    version_number=number,
                    body=body,
                    created_at=created_at,
                )
            )
        session.commit()


def _create_foreign_prompt() -> str:
    """A prompt owned by a different user, with a stored version of its own."""
    from app.models import Project, User

    with SessionLocal() as session:
        user = User(
            email=f"foreign-3i-{uuid.uuid4().hex[:10]}@sparkprompt.local",
            display_name="Foreign workspace owner",
        )
        session.add(user)
        session.flush()
        project = Project(user_id=user.id, name="Foreign workspace")
        session.add(project)
        session.flush()
        prompt = Prompt(
            project_id=project.id, title="Foreign confidential prompt", idea="not yours"
        )
        session.add(prompt)
        session.flush()
        session.add(
            PromptVersion(prompt_id=prompt.id, version_number=1, body="foreign secret body")
        )
        session.commit()
        return str(prompt.id)


def _evaluator() -> dict:
    return {
        "name": "versioning check",
        "rules": [{"type": "contains", "text": "Simulated answer"}],
    }


def _require_db(db_ready: bool) -> None:
    if not db_ready:
        pytest.skip("Postgres not reachable")


# ------------------------------------------------------------------ A / B: create + v1


def test_create_prompt_without_body_persists_no_version(db_ready):
    """(A) A prompt created without a body has no version at all."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client)
    prompt_id = created["id"]
    assert created["body"] is None
    assert created["version_number"] is None
    assert _versions(prompt_id) == []


def test_create_prompt_with_body_persists_version_1(db_ready):
    """(A)(B) The first save creates version 1 and the response reports it."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, body="Write a short email")
    prompt_id = created["id"]
    assert created["body"] == "Write a short email"
    assert created["version_number"] == 1
    assert _versions(prompt_id) == [(1, "Write a short email")]


def test_create_prompt_with_empty_body_persists_no_version(db_ready):
    """(B) An empty body is "no body", matching the pre-3I create convention."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, body="")
    assert created["body"] is None
    assert created["version_number"] is None
    assert _versions(created["id"]) == []


# ------------------------------------------------------------- C: metadata-only update


def test_update_metadata_only_creates_no_version(db_ready):
    """(C) A metadata-only update leaves the version history untouched."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, body="Write a short email")
        prompt_id = created["id"]
        before = _version_snapshot(prompt_id)

        response = client.put(
            f"/api/prompts/{prompt_id}",
            json={
                "title": "Renamed prompt",
                "idea": "A clearer idea",
                "audience": "developer",
                "output_format": "list",
                "depth": 3,
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Renamed prompt"
    assert body["idea"] == "A clearer idea"
    assert body["audience"] == "developer"
    assert body["output_format"] == "list"
    assert body["depth"] == 3
    assert body["body"] == "Write a short email"
    assert body["version_number"] == 1
    assert _version_snapshot(prompt_id) == before


def test_metadata_update_on_versionless_prompt_keeps_it_versionless(db_ready):
    """(C) A prompt with no versions stays versionless after a metadata update."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client)
        prompt_id = created["id"]
        response = client.put(f"/api/prompts/{prompt_id}", json={"title": "Only a title"})
    assert response.status_code == 200, response.text
    assert response.json()["version_number"] is None
    assert response.json()["body"] is None
    assert _versions(prompt_id) == []


# --------------------------------------------------------- D / E / F: body appends


def test_update_body_creates_version_2(db_ready):
    """(D) A body update appends v2; v1 is still stored verbatim."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, body="Write a short email")
        prompt_id = created["id"]

        response = client.put(
            f"/api/prompts/{prompt_id}", json={"body": "Write a professional email"}
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["body"] == "Write a professional email"
    assert body["version_number"] == 2
    assert _versions(prompt_id) == [
        (1, "Write a short email"),
        (2, "Write a professional email"),
    ]


def test_update_body_creates_version_3(db_ready):
    """(E) A third save appends v3 without disturbing v1 or v2."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, body="Write a short email")
        prompt_id = created["id"]
        client.put(f"/api/prompts/{prompt_id}", json={"body": "Write a professional email"})
        response = client.put(
            f"/api/prompts/{prompt_id}", json={"body": "Write a professional sales email"}
        )

    assert response.status_code == 200, response.text
    assert response.json()["version_number"] == 3
    assert _versions(prompt_id) == [
        (1, "Write a short email"),
        (2, "Write a professional email"),
        (3, "Write a professional sales email"),
    ]


def test_previous_versions_are_never_mutated(db_ready):
    """(F) Stored version rows are byte-identical after later versions are appended."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, body="Write a short email")
        prompt_id = created["id"]
        client.put(f"/api/prompts/{prompt_id}", json={"body": "Write a professional email"})
        before = _version_snapshot(prompt_id)

        client.put(f"/api/prompts/{prompt_id}", json={"body": "Write a professional sales email"})
        client.put(f"/api/prompts/{prompt_id}", json={"body": "Write a formal sales email"})

    after = _version_snapshot(prompt_id)
    assert len(after) == 4
    # Same rows, same ids, same numbers, same bodies, same created_at — only appends happened.
    assert after[:2] == before
    assert [(number, body) for _, number, body, _ in after] == [
        (1, "Write a short email"),
        (2, "Write a professional email"),
        (3, "Write a professional sales email"),
        (4, "Write a formal sales email"),
    ]


def test_body_update_on_versionless_prompt_starts_at_version_1(db_ready):
    """(D) ``max(version_number) + 1`` over no versions is 1."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client)
        prompt_id = created["id"]
        response = client.put(f"/api/prompts/{prompt_id}", json={"body": "First real body"})
    assert response.status_code == 200, response.text
    assert response.json()["version_number"] == 1
    assert _versions(prompt_id) == [(1, "First real body")]


# ------------------------------------------------------- G: latest body in the response


def test_latest_body_is_reported_by_update_get_and_list(db_ready):
    """(G) PUT, GET and LIST all agree on the newest version's body and number."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, body="Write a short email")
        prompt_id = created["id"]
        client.put(f"/api/prompts/{prompt_id}", json={"body": "Write a professional email"})

        put_body = client.put(
            f"/api/prompts/{prompt_id}", json={"body": "Write a professional sales email"}
        ).json()
        get_body = client.get(f"/api/prompts/{prompt_id}").json()
        listed = client.get("/api/prompts").json()

    assert put_body["body"] == get_body["body"] == "Write a professional sales email"
    assert put_body["version_number"] == get_body["version_number"] == 3

    row = next(item for item in listed if item["id"] == prompt_id)
    assert row["body"] == "Write a professional sales email"
    assert row["version_number"] == 3


def test_update_also_persists_metadata_alongside_the_new_version(db_ready):
    """(G)(STEP 3) Metadata and body can change in the same request."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, body="Write a short email")
        prompt_id = created["id"]
        response = client.put(
            f"/api/prompts/{prompt_id}",
            json={"title": "Both at once", "audience": "business", "body": "Second body"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Both at once"
    assert body["audience"] == "business"
    assert body["body"] == "Second body"
    assert body["version_number"] == 2
    assert _versions(prompt_id) == [(1, "Write a short email"), (2, "Second body")]


# --------------------------------------------------------------------- H: ordering


def test_version_ordering_ignores_created_at(db_ready):
    """(H) "Latest" is the highest version_number, never the newest ``created_at``.

    ``created_at`` is deliberately arranged so that it points at a *different* row than
    the highest version number. Any ordering that leaned on insertion time would answer
    "two" here; version-number ordering answers "three".
    """
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client)
        prompt_id = created["id"]

    _insert_versions(
        prompt_id,
        [
            (1, "one", datetime(2020, 1, 1, tzinfo=timezone.utc)),
            (2, "two", datetime(2024, 1, 1, tzinfo=timezone.utc)),
            (3, "three", datetime(2021, 1, 1, tzinfo=timezone.utc)),
        ],
    )

    with _client() as client:
        fetched = client.get(f"/api/prompts/{prompt_id}").json()
        # max + 1 = 4, not count + 1 and not "after the newest timestamp".
        updated = client.put(f"/api/prompts/{prompt_id}", json={"body": "four"})

    assert fetched["body"] == "three"
    assert fetched["version_number"] == 3
    assert updated.status_code == 200, updated.text
    assert updated.json()["version_number"] == 4
    assert _versions(prompt_id) == [(1, "one"), (2, "two"), (3, "three"), (4, "four")]


def test_version_numbers_are_sequential_and_gapless(db_ready):
    """(H) Numbering is deterministic: 1, 2, 3, ... with no gaps or reuse."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, body="v1")
        prompt_id = created["id"]
        reported = [created["version_number"]]
        for index in range(2, 6):
            response = client.put(f"/api/prompts/{prompt_id}", json={"body": f"v{index}"})
            reported.append(response.json()["version_number"])

    assert reported == [1, 2, 3, 4, 5]
    assert [number for number, _ in _versions(prompt_id)] == [1, 2, 3, 4, 5]


# ------------------------------------------------------------------ I / J: ownership


def test_update_unknown_prompt_returns_404(db_ready):
    """(I) An unknown prompt id is a clean 404 that leaks nothing."""
    _require_db(db_ready)
    missing = str(uuid.uuid4())
    with _client() as client:
        response = client.put(f"/api/prompts/{missing}", json={"body": "should not persist"})
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Prompt not found"}
    assert _versions(missing) == []


def test_update_foreign_prompt_returns_404_without_leaking(db_ready):
    """(J) A foreign prompt is indistinguishable from a missing one."""
    _require_db(db_ready)
    foreign_id = _create_foreign_prompt()
    before = _version_snapshot(foreign_id)

    with _client() as client:
        response = client.put(
            f"/api/prompts/{foreign_id}", json={"body": "stolen", "title": "stolen"}
        )

    assert response.status_code == 404, response.text
    # Byte-identical to the unknown-prompt envelope: no title, body, count or ids.
    assert response.json() == {"detail": "Prompt not found"}
    for leak in ("foreign secret body", "Foreign confidential prompt", "version", "stolen"):
        assert leak not in response.text
    # Nothing was written on the foreign prompt.
    assert _version_snapshot(foreign_id) == before
    with SessionLocal() as session:
        title = session.get(Prompt, uuid.UUID(foreign_id)).title
    assert title == "Foreign confidential prompt"


def test_owner_can_still_update_their_own_prompt(db_ready):
    """(STEP 5) The owner path is unaffected by the ownership-first refactor."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, body="owner body")
        response = client.put(
            f"/api/prompts/{created['id']}",
            json={"title": "owner renamed", "body": "owner body v2"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "owner renamed"
    assert response.json()["version_number"] == 2


# ---------------------------------------------------------------- K: body validation


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({"body": ""}, "empty body is a no-op, exactly as on create"),
        ({"body": None}, "explicit null is a no-op"),
    ],
)
def test_falsy_body_does_not_create_a_version(db_ready, payload, why):
    """(K) A falsy body preserves the create-side convention: no new version."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, body="original body")
        prompt_id = created["id"]
        before = _version_snapshot(prompt_id)
        response = client.put(f"/api/prompts/{prompt_id}", json=payload)

    assert response.status_code == 200, response.text
    assert response.json()["body"] == "original body"
    assert response.json()["version_number"] == 1
    assert _version_snapshot(prompt_id) == before, why


@pytest.mark.parametrize(
    "payload",
    [
        {"title": ""},
        {"title": "x" * 201},
        {"depth": 0},
        {"depth": 4},
        {"audience": "y" * 41},
        {"output_format": "z" * 41},
    ],
)
def test_invalid_metadata_is_422_and_creates_no_version(db_ready, payload):
    """(K) Existing validation still rejects bad metadata, and nothing is written."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, body="original body")
        prompt_id = created["id"]
        before = _version_snapshot(prompt_id)
        # A valid body rides along; the 422 must reject the whole request.
        response = client.put(f"/api/prompts/{prompt_id}", json={**payload, "body": "new body"})

    assert response.status_code == 422, response.text
    assert _version_snapshot(prompt_id) == before
    fetched = _client().get(f"/api/prompts/{prompt_id}").json()
    assert fetched["body"] == "original body"


def test_whitespace_only_body_is_still_a_version(db_ready):
    """(K) Documented boundary: only a *falsy* body is skipped, matching create.

    ``create_prompt`` has always stored any non-empty string, so ``update_prompt`` stores
    any non-empty string too. Consistency with the existing writer is deliberate: adding
    stricter validation to the update path only would be a silent contract change.
    """
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, body="original body")
        response = client.put(f"/api/prompts/{created['id']}", json={"body": "   "})
    assert response.status_code == 200, response.text
    assert response.json()["version_number"] == 2
    assert _versions(created["id"])[1] == (2, "   ")


# -------------------------------------------------------------------- L: many updates


def test_repeated_updates_accumulate_a_history(db_ready):
    """(L) Six saves produce exactly six versions, numbered 1..6."""
    _require_db(db_ready)
    bodies = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
    with _client() as client:
        created = _create_prompt(client, body=bodies[0])
        prompt_id = created["id"]
        for body in bodies[1:]:
            assert client.put(f"/api/prompts/{prompt_id}", json={"body": body}).status_code == 200

    assert _versions(prompt_id) == [(index + 1, body) for index, body in enumerate(bodies)]


def test_interleaved_metadata_and_body_updates_keep_one_history(db_ready):
    """(L) Metadata edits do not consume a version number."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, body="first")
        prompt_id = created["id"]
        client.put(f"/api/prompts/{prompt_id}", json={"title": "renamed once"})
        client.put(f"/api/prompts/{prompt_id}", json={"body": "second"})
        client.put(f"/api/prompts/{prompt_id}", json={"depth": 1})
        client.put(f"/api/prompts/{prompt_id}", json={"body": "third"})

    assert _versions(prompt_id) == [(1, "first"), (2, "second"), (3, "third")]


# -------------------------------------------------------------- M: atomic transaction


def test_failed_version_write_rolls_back_the_metadata_change(db_ready, demo_identity, monkeypatch):
    """(M) A metadata change and a new version are one commit: both or neither."""
    _require_db(db_ready)
    from app.schemas.prompt import PromptUpdate
    from app.services import prompts as prompts_service

    with _client() as client:
        created = _create_prompt(client, body="original body")
    prompt_id = uuid.UUID(created["id"])

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated version-write failure")

    # Fails after the in-memory metadata mutation but before the single commit.
    monkeypatch.setattr(prompts_service, "_next_version_number", boom)

    with SessionLocal() as session:
        # 4A: direct service calls resolve their owner from session.info (fail-closed).
        session.info[IDENTITY_KEY] = demo_identity
        with pytest.raises(RuntimeError, match="simulated version-write failure"):
            prompts_service.update_prompt(
                session, prompt_id, PromptUpdate(title="Should not stick", body="v2")
            )
        session.rollback()

    assert _versions(str(prompt_id)) == [(1, "original body")]
    with _client() as client:
        after = client.get(f"/api/prompts/{prompt_id}").json()
    assert after["title"] == created["title"]
    assert after["body"] == "original body"
    assert after["version_number"] == 1


def test_update_emits_a_row_lock(db_ready):
    """(STEP 12) The version-append path locks the prompt row.

    Reading the highest version number and then inserting the next one is a read-then-write
    sequence. The lock makes it atomic per prompt without adding a schema constraint.
    """
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, body="v1")
        prompt_id = created["id"]

    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    with _client() as client:
        event.listen(engine, "before_cursor_execute", capture)
        try:
            response = client.put(f"/api/prompts/{prompt_id}", json={"body": "v2"})
        finally:
            event.remove(engine, "before_cursor_execute", capture)

    assert response.status_code == 200, response.text
    assert any("FOR UPDATE" in statement.upper() for statement in statements), statements


def test_concurrent_body_updates_get_distinct_version_numbers(db_ready, demo_identity):
    """(STEP 12) Two real threads racing on one prompt never share a version number."""
    _require_db(db_ready)
    from app.schemas.prompt import PromptUpdate
    from app.services import prompts as prompts_service

    with _client() as client:
        created = _create_prompt(client, body="seed body")
    prompt_id = uuid.UUID(created["id"])

    barrier = threading.Barrier(2)
    failures: list[BaseException] = []

    def append(body: str) -> None:
        try:
            barrier.wait(timeout=10)
            with SessionLocal() as session:
                session.info[IDENTITY_KEY] = demo_identity
                prompts_service.update_prompt(session, prompt_id, PromptUpdate(body=body))
        except BaseException as exc:  # noqa: BLE001 - surfaced as a failure below
            failures.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(append, ["racer one", "racer two"]))

    assert not failures, failures
    versions = _versions(str(prompt_id))
    numbers = [number for number, _ in versions]
    assert numbers == [1, 2, 3], versions
    assert len(set(numbers)) == len(numbers)
    assert {body for _, body in versions} == {"seed body", "racer one", "racer two"}


# ------------------------------------------------------------------ N: CRUD regression


def test_existing_crud_still_works_end_to_end(db_ready):
    """(N) create -> list -> get -> update -> delete, with cascade on delete."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, body="crud body")
        prompt_id = created["id"]

        assert any(item["id"] == prompt_id for item in client.get("/api/prompts").json())
        assert client.get(f"/api/prompts/{prompt_id}").json()["body"] == "crud body"

        renamed = client.put(f"/api/prompts/{prompt_id}", json={"title": "crud renamed"})
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "crud renamed"

        deleted = client.delete(f"/api/prompts/{prompt_id}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {"message": "Prompt deleted"}
        assert client.get(f"/api/prompts/{prompt_id}").status_code == 404

    assert _versions(prompt_id) == []


def test_prompt_and_version_models_are_unchanged(db_ready):
    """(STEP 2) No column was added, renamed or removed, and no table is new."""
    _require_db(db_ready)
    assert set(Prompt.__table__.columns.keys()) == {
        "id",
        "project_id",
        "title",
        "idea",
        "audience",
        "output_format",
        "depth",
        "created_at",
        "updated_at",
    }
    assert set(PromptVersion.__table__.columns.keys()) == {
        "id",
        "prompt_id",
        "version_number",
        "body",
        "created_at",
    }
    inspector = inspect_tables()
    assert "prompt_versions" in inspector
    assert not any("experiment" in name for name in inspector)


def inspect_tables() -> set[str]:
    from sqlalchemy import inspect as sa_inspect

    return set(sa_inspect(engine).get_table_names())


def test_create_prompt_version_does_not_touch_prompt_runs(db_ready):
    """(STEP 2) Versioning is independent of PromptRun: saving adds no run."""
    _require_db(db_ready)
    from app.models import PromptRun

    with _client() as client:
        created = _create_prompt(client, body="no run here")
        prompt_id = uuid.UUID(created["id"])
        client.put(f"/api/prompts/{prompt_id}", json={"body": "still no run"})
        client.put(f"/api/prompts/{prompt_id}", json={"body": "nor here"})

        with SessionLocal() as session:
            runs = session.scalars(select(PromptRun).where(PromptRun.prompt_id == prompt_id)).all()
    assert runs == []


# ------------------------------------------------- O: Phase 3H integration (read-only)


def test_phase_3h_experiment_discovers_api_created_versions(db_ready, app_client):
    """(O) The spec's end-to-end flow: v1, v2, v3 from the API, then a 3H experiment.

    The experiment is run unchanged — nothing in Phase 3H was touched. ``output`` carries
    the evaluated text, so matching each output to the body that was saved proves the three
    saved versions were the three measured versions, in order.
    """
    _require_db(db_ready)
    v1 = "Write a short email"
    v2 = "Write a professional email"
    v3 = "Write a professional sales email"

    created = _create_prompt(app_client, body=v1)
    prompt_id = created["id"]
    assert created["version_number"] == 1
    assert app_client.put(f"/api/prompts/{prompt_id}", json={"body": v2}).json()[
        "version_number"
    ] == 2
    assert app_client.put(f"/api/prompts/{prompt_id}", json={"body": v3}).json()[
        "version_number"
    ] == 3

    # 7. retrieve -> 8. latest body is version 3
    fetched = app_client.get(f"/api/prompts/{prompt_id}").json()
    assert fetched["body"] == v3
    assert fetched["version_number"] == 3

    # 9. versions 1 and 2 remain unchanged
    assert _versions(prompt_id) == [(1, v1), (2, v2), (3, v3)]

    response = app_client.post(
        "/api/experiments/run", json={"prompt_id": prompt_id, "evaluator": _evaluator()}
    )
    assert response.status_code == 200, response.text
    result = response.json()

    # 11. total_versions = 3
    assert result["total_versions"] == 3
    assert result["passed"] == 3
    evaluations = result["evaluations"]
    assert len(evaluations) == 3
    # 12. evaluations correspond to the three saved bodies, oldest first.
    for evaluation, body in zip(evaluations, (v1, v2, v3), strict=True):
        assert body in evaluation["output"]
        assert evaluation["passed"] is True
        assert evaluation["run_id"] is not None


def test_phase_3h_experiment_sees_no_extra_versions_after_metadata_edits(db_ready, app_client):
    """(O) Metadata-only edits add no version, so 3H's count is unaffected by them."""
    _require_db(db_ready)
    created = _create_prompt(app_client, body="only body change")
    prompt_id = created["id"]
    app_client.put(f"/api/prompts/{prompt_id}", json={"title": "renamed"})
    app_client.put(f"/api/prompts/{prompt_id}", json={"body": "second body"})

    response = app_client.post(
        "/api/experiments/run", json={"prompt_id": prompt_id, "evaluator": _evaluator()}
    )
    assert response.status_code == 200, response.text
    assert response.json()["total_versions"] == 2
