"""Phase 3J version-history + restore tests.

Covers the Phase 3J acceptance matrix (A-V). Same conventions as
``test_prompt_versioning.py``: the ``integration`` marker, a module-scoped
``db_ready`` reachability check that skips cleanly when Postgres is down, plain
``TestClient`` usage, and direct ``SessionLocal`` reads whenever a claim is about
what is actually stored rather than what the API returned.

Load-bearing claims proved here rather than assumed:

* **History ordering never consults ``created_at``** — seeded rows whose timestamps
  contradict their numbers still come back in ``version_number`` order.
* **Restore cannot collide under concurrency** — a restore racing a body update on
  the same prompt yields distinct, sequential version numbers via the Phase 3I
  row lock and the shared ``_next_version_number`` helper.
* **No mutation** — full-row snapshots before/after restore prove the source row is
  byte-identical and the new row is a different row.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal, engine
from app.models import Prompt, PromptVersion
from app.services.identity import IDENTITY_KEY

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


def _require_db(db_ready: bool) -> None:
    if not db_ready:
        pytest.skip("Postgres not reachable")


def _create_prompt(client, body: str | None = None, **overrides) -> dict:
    payload = {
        "title": overrides.pop("title", f"3J history {uuid.uuid4().hex[:8]}"),
        "idea": overrides.pop("idea", "Inspect and restore saved prompt versions."),
    }
    payload.update(overrides)
    if body is not None:
        payload["body"] = body
    response = client.post("/api/prompts", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _create_foreign_prompt() -> tuple[str, str]:
    """A foreign-owned prompt with one stored version. Returns (prompt_id, version_id)."""
    from app.models import Project, User

    with SessionLocal() as session:
        user = User(
            email=f"foreign-3j-{uuid.uuid4().hex[:10]}@sparkprompt.local",
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
        version = PromptVersion(
            prompt_id=prompt.id, version_number=1, body="foreign secret body"
        )
        session.add(version)
        session.flush()
        session.commit()
        return str(prompt.id), str(version.id)


def _versions(prompt_id: str) -> list[tuple[int, str]]:
    with SessionLocal() as session:
        rows = session.execute(
            select(PromptVersion.version_number, PromptVersion.body)
            .where(PromptVersion.prompt_id == uuid.UUID(prompt_id))
            .order_by(PromptVersion.version_number.asc())
        ).all()
    return [(int(number), body) for number, body in rows]


def _version_snapshot(prompt_id: str) -> list[tuple[uuid.UUID, int, str, datetime]]:
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


def _make_three(client) -> tuple[str, str, str, str]:
    """Returns (prompt_id, v1_id, v2_id, v3_id) for bodies A/B/C."""
    created = _create_prompt(client, body="Write a short email")
    prompt_id = created["id"]
    v1_id = next(
        item["id"]
        for item in client.get(f"/api/prompts/{prompt_id}/versions").json()
        if item["version_number"] == 1
    )
    client.put(f"/api/prompts/{prompt_id}", json={"body": "Write a professional email"})
    client.put(f"/api/prompts/{prompt_id}", json={"body": "Write a professional sales email"})
    history = client.get(f"/api/prompts/{prompt_id}/versions").json()
    by_number = {item["version_number"]: item["id"] for item in history}
    return prompt_id, v1_id, by_number[2], by_number[3]


# ------------------------------------------------------------- A: owned history read


def test_history_for_owned_prompt_returns_all_versions(db_ready):
    """(A)(F) An owned prompt's full history, with bodies, numbers, ids and timestamps."""
    _require_db(db_ready)
    with _client() as client:
        prompt_id, _, _, _ = _make_three(client)
        response = client.get(f"/api/prompts/{prompt_id}/versions")

    assert response.status_code == 200, response.text
    history = response.json()
    assert [item["version_number"] for item in history] == [1, 2, 3]
    assert [item["body"] for item in history] == [
        "Write a short email",
        "Write a professional email",
        "Write a professional sales email",
    ]
    for item in history:
        assert set(item.keys()) == {"id", "version_number", "body", "created_at"}
        uuid.UUID(item["id"])  # parses as a UUID
        assert item["created_at"]  # parses as a non-empty datetime string
        datetime.fromisoformat(item["created_at"])
    assert len({item["id"] for item in history}) == 3


def test_history_on_versionless_prompt_is_empty(db_ready):
    """(A) A prompt with no versions has an empty history, not a 404."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client)
        response = client.get(f"/api/prompts/{created['id']}/versions")
    assert response.status_code == 200, response.text
    assert response.json() == []


# ------------------------------------------------------- B/C: unknown + foreign reads


def test_history_unknown_prompt_returns_404(db_ready):
    """(B) An unknown prompt id is a clean 404."""
    _require_db(db_ready)
    missing = str(uuid.uuid4())
    with _client() as client:
        response = client.get(f"/api/prompts/{missing}/versions")
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Prompt not found"}


def test_history_foreign_prompt_returns_404_without_leaking(db_ready):
    """(C) A foreign prompt is indistinguishable from a missing one."""
    _require_db(db_ready)
    foreign_id, _ = _create_foreign_prompt()

    with _client() as client:
        response = client.get(f"/api/prompts/{foreign_id}/versions")

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Prompt not found"}
    for leak in ("foreign secret body", "Foreign confidential prompt", "version"):
        assert leak not in response.text


# --------------------------------------------------------------- D/E: ordering rules


def test_history_order_ignores_created_at(db_ready):
    """(D) History is ordered by ``version_number``, never ``created_at``."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client)

    with SessionLocal() as session:
        for number, body, created_at in [
            (1, "one", datetime(2020, 1, 1, tzinfo=timezone.utc)),
            (2, "two", datetime(2024, 1, 1, tzinfo=timezone.utc)),
            (3, "three", datetime(2021, 1, 1, tzinfo=timezone.utc)),
        ]:
            session.add(
                PromptVersion(
                    prompt_id=uuid.UUID(created["id"]),
                    version_number=number,
                    body=body,
                    created_at=created_at,
                )
            )
        session.commit()

    with _client() as client:
        history = client.get(f"/api/prompts/{created['id']}/versions").json()
    assert [item["body"] for item in history] == ["one", "two", "three"]


def test_history_id_tiebreak_is_deterministic(db_ready):
    """(E) Same ``version_number`` twice still yields a deterministic order (id ASC)."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, body="seed")
    prompt_id = created["id"]

    try:
        with SessionLocal() as session:
            first = PromptVersion(
                prompt_id=uuid.UUID(prompt_id), version_number=7, body="tie one"
            )
            second = PromptVersion(
                prompt_id=uuid.UUID(prompt_id), version_number=7, body="tie two"
            )
            session.add_all([first, second])
            session.commit()
            first_id, second_id = str(first.id), str(second.id)

        with _client() as client:
            history = client.get(f"/api/prompts/{prompt_id}/versions").json()
        tied = [item for item in history if item["version_number"] == 7]
        assert [item["id"] for item in tied] == sorted(
            [first_id, second_id], key=lambda _id: uuid.UUID(_id).int
        )
        bodies_by_id = {item["id"]: item["body"] for item in tied}
        assert bodies_by_id[first_id] == "tie one"
        assert bodies_by_id[second_id] == "tie two"
    finally:
        # The duplicate-number rows exist only to exercise the tie-break; remove the
        # fixture prompt (cascade removes its versions) so the suite leaves no
        # deliberate numbering violations behind.
        with _client() as client:
            client.delete(f"/api/prompts/{prompt_id}")


# ------------------------------------------------------- G/H/I/J/K/L: restore core


def test_restore_v1_appends_v4(db_ready):
    """(G)(J) Restoring v1 of a three-version prompt creates v4 with v1's body."""
    _require_db(db_ready)
    with _client() as client:
        prompt_id, v1_id, _, _ = _make_three(client)
        before = _version_snapshot(prompt_id)

        response = client.post(f"/api/prompts/{prompt_id}/versions/{v1_id}/restore")

    assert response.status_code == 201, response.text
    created = response.json()
    assert set(created.keys()) == {"id", "version_number", "body", "created_at"}
    assert created["version_number"] == 4
    assert created["body"] == "Write a short email"
    assert created["id"] != v1_id

    after = _version_snapshot(prompt_id)
    assert len(after) == 4
    assert after[:3] == before  # historical rows untouched
    assert (after[3][1], after[3][2]) == (4, "Write a short email")
    assert after[3][0] != before[0][0]


def test_restore_middle_version(db_ready):
    """(H) Restoring v2 appends a new version carrying v2's exact body."""
    _require_db(db_ready)
    with _client() as client:
        prompt_id, _, v2_id, _ = _make_three(client)
        response = client.post(f"/api/prompts/{prompt_id}/versions/{v2_id}/restore")

    assert response.status_code == 201, response.text
    assert response.json()["version_number"] == 4
    assert response.json()["body"] == "Write a professional email"
    assert _versions(prompt_id) == [
        (1, "Write a short email"),
        (2, "Write a professional email"),
        (3, "Write a professional sales email"),
        (4, "Write a professional email"),
    ]


def test_restore_latest_version(db_ready):
    """(I) Restoring the latest version still appends — it never becomes a no-op."""
    _require_db(db_ready)
    with _client() as client:
        prompt_id, _, _, v3_id = _make_three(client)
        response = client.post(f"/api/prompts/{prompt_id}/versions/{v3_id}/restore")

    assert response.status_code == 201, response.text
    assert response.json()["version_number"] == 4
    assert _versions(prompt_id)[3] == (4, "Write a professional sales email")


def test_restore_leaves_source_row_byte_identical(db_ready):
    """(K)(L) The source row is unchanged; the new row is a different row, same body."""
    _require_db(db_ready)
    with _client() as client:
        prompt_id, v1_id, _, _ = _make_three(client)
        before = {str(rid): (num, body, created) for rid, num, body, created in _version_snapshot(prompt_id)}

        restored = client.post(
            f"/api/prompts/{prompt_id}/versions/{v1_id}/restore"
        ).json()
        after = {str(rid): (num, body, created) for rid, num, body, created in _version_snapshot(prompt_id)}

    # Every pre-existing row is byte-identical.
    for rid, row in before.items():
        assert after[rid] == row
    # The new row is a different id, a different number, the exact same body.
    assert restored["id"] not in before
    assert restored["body"] == before[v1_id][1]
    assert restored["version_number"] != before[v1_id][0]


def test_restore_updates_latest_body(db_ready):
    """(STEP 15) After a restore, GET reports the restored body as the latest."""
    _require_db(db_ready)
    with _client() as client:
        prompt_id, v1_id, _, _ = _make_three(client)
        client.post(f"/api/prompts/{prompt_id}/versions/{v1_id}/restore")
        fetched = client.get(f"/api/prompts/{prompt_id}").json()

    assert fetched["body"] == "Write a short email"
    assert fetched["version_number"] == 4


# ------------------------------------------------- M/N/O/P: safe 404 matrix


def test_restore_unknown_prompt_returns_404(db_ready):
    """(M) Restoring against an unknown prompt is the standard prompt 404."""
    _require_db(db_ready)
    with _client() as client:
        response = client.post(
            f"/api/prompts/{uuid.uuid4()}/versions/{uuid.uuid4()}/restore"
        )
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Prompt not found"}


def test_restore_foreign_prompt_returns_404_without_leaking(db_ready):
    """(N) A foreign prompt fails before any version is read or written."""
    _require_db(db_ready)
    foreign_id, foreign_version_id = _create_foreign_prompt()
    before = _version_snapshot(foreign_id)

    with _client() as client:
        response = client.post(
            f"/api/prompts/{foreign_id}/versions/{foreign_version_id}/restore"
        )

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Prompt not found"}
    for leak in ("foreign secret body", "Foreign confidential prompt", "version"):
        assert leak not in response.text
    assert _version_snapshot(foreign_id) == before


def test_restore_unknown_version_returns_safe_404(db_ready):
    """(O) A version id that belongs nowhere is a 404 that creates nothing."""
    _require_db(db_ready)
    with _client() as client:
        prompt_id, _, _, _ = _make_three(client)
        before = _version_snapshot(prompt_id)
        response = client.post(
            f"/api/prompts/{prompt_id}/versions/{uuid.uuid4()}/restore"
        )

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Prompt version not found"}
    assert _version_snapshot(prompt_id) == before


def test_cross_prompt_restore_is_rejected(db_ready):
    """(P) A version id from prompt B cannot be restored into prompt A."""
    _require_db(db_ready)
    with _client() as client:
        prompt_a, _, _, _ = _make_three(client)
        created_b = _create_prompt(client, body="prompt B private body")
        prompt_b = created_b["id"]
        version_b = client.get(f"/api/prompts/{prompt_b}/versions").json()[0]["id"]

        before_a = _version_snapshot(prompt_a)
        before_b = _version_snapshot(prompt_b)

        response = client.post(f"/api/prompts/{prompt_a}/versions/{version_b}/restore")

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Prompt version not found"}
    assert "prompt B private body" not in response.text
    assert _version_snapshot(prompt_a) == before_a
    assert _version_snapshot(prompt_b) == before_b


def test_cross_prompt_restore_both_directions(db_ready):
    """(P) The protection is symmetric: B-into-A and A-into-B both fail safely."""
    _require_db(db_ready)
    with _client() as client:
        prompt_a, v1_a, _, _ = _make_three(client)
        created_b = _create_prompt(client, body="prompt B private body")
        prompt_b = created_b["id"]
        version_b = client.get(f"/api/prompts/{prompt_b}/versions").json()[0]["id"]

        reverse = client.post(f"/api/prompts/{prompt_b}/versions/{v1_a}/restore")

    assert reverse.status_code == 404, reverse.text
    assert reverse.json() == {"detail": "Prompt version not found"}
    assert _versions(prompt_a) == [
        (1, "Write a short email"),
        (2, "Write a professional email"),
        (3, "Write a professional sales email"),
    ]
    assert _versions(prompt_b) == [(1, "prompt B private body")]


# ------------------------------------------------------ Q/R/S: atomicity + races


def test_failed_restore_rolls_back_cleanly(db_ready, demo_identity, monkeypatch):
    """(Q) If the new version cannot be written, nothing changes at all."""
    _require_db(db_ready)
    from app.services import prompts as prompts_service

    with _client() as client:
        prompt_id, v1_id, _, _ = _make_three(client)
    before = _version_snapshot(prompt_id)

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated restore-write failure")

    monkeypatch.setattr(prompts_service, "_next_version_number", boom)

    with SessionLocal() as session:
        # 4A: direct service calls resolve their owner from session.info (fail-closed).
        session.info[IDENTITY_KEY] = demo_identity
        with pytest.raises(RuntimeError, match="simulated restore-write failure"):
            prompts_service.restore_prompt_version(
                session, uuid.UUID(prompt_id), uuid.UUID(v1_id)
            )
        session.rollback()

    assert _version_snapshot(prompt_id) == before


def test_restore_emits_a_row_lock(db_ready):
    """(STEP 10) Restore reuses the Phase 3I row lock — verified in emitted SQL."""
    _require_db(db_ready)
    from sqlalchemy import event

    with _client() as client:
        prompt_id, v1_id, _, _ = _make_three(client)

    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    with _client() as client:
        event.listen(engine, "before_cursor_execute", capture)
        try:
            response = client.post(f"/api/prompts/{prompt_id}/versions/{v1_id}/restore")
        finally:
            event.remove(engine, "before_cursor_execute", capture)

    assert response.status_code == 201, response.text
    assert any("FOR UPDATE" in statement.upper() for statement in statements), statements


def test_concurrent_restore_and_body_update_stay_sequential(db_ready, demo_identity):
    """(R) A restore racing a body update on one prompt never shares a number."""
    _require_db(db_ready)
    from app.schemas.prompt import PromptUpdate
    from app.services import prompts as prompts_service

    with _client() as client:
        created = _create_prompt(client, body="seed body")
    prompt_id = uuid.UUID(created["id"])
    with SessionLocal() as session:
        source_id = session.execute(
            select(PromptVersion.id).where(PromptVersion.prompt_id == prompt_id)
        ).scalar_one()

    barrier = threading.Barrier(2)
    failures: list[BaseException] = []

    def do_restore() -> None:
        try:
            barrier.wait(timeout=10)
            with SessionLocal() as session:
                session.info[IDENTITY_KEY] = demo_identity
                prompts_service.restore_prompt_version(session, prompt_id, source_id)
        except BaseException as exc:  # noqa: BLE001 - surfaced as a failure below
            failures.append(exc)

    def do_update() -> None:
        try:
            barrier.wait(timeout=10)
            with SessionLocal() as session:
                session.info[IDENTITY_KEY] = demo_identity
                prompts_service.update_prompt(
                    session, prompt_id, PromptUpdate(body="racer body")
                )
        except BaseException as exc:  # noqa: BLE001 - surfaced as a failure below
            failures.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda fn: fn(), [do_restore, do_update]))

    assert not failures, failures
    versions = _versions(str(prompt_id))
    numbers = [number for number, _ in versions]
    assert numbers == [1, 2, 3], versions
    # Exactly one body update and one restore happened: the seed body appears twice
    # (original v1 plus the restored copy) alongside the racer body.
    assert sorted(body for _, body in versions) == sorted(
        ["seed body", "seed body", "racer body"]
    )


def test_repeated_restores_accumulate(db_ready):
    """(S) Restoring v1 twice yields v4 = A and v5 = A; v1 never moves."""
    _require_db(db_ready)
    with _client() as client:
        prompt_id, v1_id, _, _ = _make_three(client)
        first = client.post(f"/api/prompts/{prompt_id}/versions/{v1_id}/restore")
        second = client.post(f"/api/prompts/{prompt_id}/versions/{v1_id}/restore")

    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["version_number"] == 4
    assert second.json()["version_number"] == 5
    assert first.json()["id"] != second.json()["id"]
    assert _versions(prompt_id) == [
        (1, "Write a short email"),
        (2, "Write a professional email"),
        (3, "Write a professional sales email"),
        (4, "Write a short email"),
        (5, "Write a short email"),
    ]


# ------------------------------------------------- T: Phase 3H discovers restores


def test_phase_3h_experiment_discovers_restored_version(db_ready, app_client):
    """(T) v1=A, v2=B, v3=C, restore v1 -> v4=A; 3H measures A/B/C/A in order."""
    _require_db(db_ready)
    v1, v2, v3 = "Write a short email", "Write a professional email", "Write a professional sales email"

    created = _create_prompt(app_client, body=v1)
    prompt_id = created["id"]
    app_client.put(f"/api/prompts/{prompt_id}", json={"body": v2})
    app_client.put(f"/api/prompts/{prompt_id}", json={"body": v3})
    history = app_client.get(f"/api/prompts/{prompt_id}/versions").json()
    v1_id = next(item["id"] for item in history if item["version_number"] == 1)

    restored = app_client.post(f"/api/prompts/{prompt_id}/versions/{v1_id}/restore")
    assert restored.status_code == 201, restored.text
    assert restored.json()["version_number"] == 4

    response = app_client.post(
        "/api/experiments/run",
        json={
            "prompt_id": prompt_id,
            "evaluator": {
                "name": "restore check",
                "rules": [{"type": "contains", "text": "Simulated answer"}],
            },
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()

    assert result["total_versions"] == 4
    evaluations = result["evaluations"]
    assert len(evaluations) == 4
    for evaluation, body in zip(evaluations, (v1, v2, v3, v1), strict=True):
        assert body in evaluation["output"]
        assert evaluation["passed"] is True


# ------------------------------------------------- U/V: side effects + regression


def test_history_and_restore_create_no_prompt_runs(db_ready):
    """(U) Reading history and restoring add versions, never PromptRuns."""
    _require_db(db_ready)
    from app.models import PromptRun

    with _client() as client:
        prompt_id, v1_id, _, _ = _make_three(client)
        client.get(f"/api/prompts/{prompt_id}/versions")
        client.post(f"/api/prompts/{prompt_id}/versions/{v1_id}/restore")

        with SessionLocal() as session:
            runs = session.scalars(
                select(PromptRun).where(PromptRun.prompt_id == uuid.UUID(prompt_id))
            ).all()
    assert runs == []


def test_existing_crud_still_works_with_history_endpoints(db_ready):
    """(V) History endpoints compose with create/get/update/delete + cascade."""
    _require_db(db_ready)
    with _client() as client:
        created = _create_prompt(client, body="crud body")
        prompt_id = created["id"]

        assert client.get(f"/api/prompts/{prompt_id}/versions").json()[0]["body"] == "crud body"
        client.put(f"/api/prompts/{prompt_id}", json={"body": "crud body v2"})
        history = client.get(f"/api/prompts/{prompt_id}/versions").json()
        assert [item["version_number"] for item in history] == [1, 2]

        restored = client.post(
            f"/api/prompts/{prompt_id}/versions/{history[0]['id']}/restore"
        )
        assert restored.status_code == 201
        assert restored.json()["version_number"] == 3

        deleted = client.delete(f"/api/prompts/{prompt_id}")
        assert deleted.status_code == 200, deleted.text
        assert client.get(f"/api/prompts/{prompt_id}/versions").status_code == 404

    assert _versions(prompt_id) == []


def test_prompt_and_version_models_are_unchanged(db_ready):
    """(STEP 2) No column was added, renamed or removed, and no table is new."""
    _require_db(db_ready)
    from sqlalchemy import inspect as sa_inspect

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
    tables = set(sa_inspect(engine).get_table_names())
    assert "prompt_versions" in tables
    assert not any("experiment" in name for name in tables)
