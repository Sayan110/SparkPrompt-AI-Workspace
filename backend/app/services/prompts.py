from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Project, Prompt, PromptVersion
from app.schemas.prompt import PromptCreate, PromptUpdate
from app.services.demo_user import get_or_create_default_project
from app.services.errors import NotFoundError
from app.services.identity import resolve_owner


def _owner_project_ids(db: Session) -> set[UUID]:
    owner = resolve_owner(db)
    return set(db.scalars(select(Project.id).where(Project.user_id == owner.id)))


def _next_version_number(db: Session, prompt_id: UUID) -> int:
    """The project's next-version rule: ``max(version_number) + 1``, or 1 when none.

    This is the rule ``create_prompt`` has always used, extracted so the create and
    update writers cannot drift apart. It is deterministic by construction: it reads
    only ``version_number``, never ``created_at``, and it never renumbers or replaces
    an existing version — a new version is always appended.
    """
    latest = db.scalar(
        select(func.max(PromptVersion.version_number)).where(
            PromptVersion.prompt_id == prompt_id
        )
    )
    return (latest or 0) + 1


def _attach_latest_bodies(db: Session, prompts: list[Prompt]) -> None:
    if not prompts:
        return
    prompt_ids = [prompt.id for prompt in prompts]
    rows = db.execute(
        select(
            PromptVersion.prompt_id,
            PromptVersion.body,
            PromptVersion.version_number,
        )
        .where(PromptVersion.prompt_id.in_(prompt_ids))
        .order_by(PromptVersion.prompt_id, PromptVersion.version_number.desc())
    ).all()
    latest: dict[UUID, tuple[str, int]] = {}
    for prompt_id, body, version_number in rows:
        if prompt_id not in latest:
            latest[prompt_id] = (body, version_number)
    for prompt in prompts:
        found = latest.get(prompt.id)
        if found is not None:
            prompt.body, prompt.version_number = found


def list_prompts(db: Session) -> list[Prompt]:
    project_ids = _owner_project_ids(db)
    if not project_ids:
        return []
    prompts = list(
        db.scalars(
            select(Prompt)
            .where(Prompt.project_id.in_(project_ids))
            .order_by(Prompt.created_at.desc())
        )
    )
    _attach_latest_bodies(db, prompts)
    return prompts


def _owned_prompt(db: Session, prompt_id: UUID) -> Prompt:
    project_ids = _owner_project_ids(db)
    prompt = db.get(Prompt, prompt_id)
    if prompt is None or prompt.project_id not in project_ids:
        raise NotFoundError("Prompt not found")
    return prompt


def _owned_prompt_for_update(db: Session, prompt_id: UUID) -> Prompt:
    """Ownership-checked ``SELECT ... FOR UPDATE``, used only by the update path.

    Appending a version is read-then-write (read the highest ``version_number``, write
    the next one), so two concurrent body edits could otherwise append the same number.
    Locking the prompt row for the rest of the transaction makes the read-then-write
    sequence atomic per prompt, without a schema change.

    ``_owned_prompt`` is deliberately left alone: only writes need the lock, and adding
    one to every read would serialize unrelated traffic. The 404 contract is identical
    — the ownership check is the same, and a foreign prompt still fails before any
    version is read or written.
    """
    project_ids = _owner_project_ids(db)
    prompt = db.scalar(select(Prompt).where(Prompt.id == prompt_id).with_for_update())
    if prompt is None or prompt.project_id not in project_ids:
        raise NotFoundError("Prompt not found")
    return prompt


def get_prompt(db: Session, prompt_id: UUID) -> Prompt:
    prompt = _owned_prompt(db, prompt_id)
    _attach_latest_bodies(db, [prompt])
    return prompt


def get_owned_prompt_version(db: Session, prompt_id: UUID, version_id: UUID) -> PromptVersion:
    """Resolve one version of an owned prompt, or raise the safe 404.

    Ownership first: an unknown or foreign prompt fails with "Prompt not
    found" before any version is read. A version id that does not belong to
    this prompt — whether it exists elsewhere or nowhere — fails with
    "Prompt version not found", revealing nothing about any other prompt.
    Versions are immutable, so resolution takes no row lock.
    """
    prompt = _owned_prompt(db, prompt_id)
    version = db.scalar(
        select(PromptVersion).where(
            PromptVersion.id == version_id,
            PromptVersion.prompt_id == prompt.id,
        )
    )
    if version is None:
        raise NotFoundError("Prompt version not found")
    return version


def _resolve_project(db: Session, project_id: UUID | None) -> Project:
    owner = resolve_owner(db)
    if project_id is None:
        return get_or_create_default_project(db, owner)
    project = db.get(Project, project_id)
    if project is None or project.user_id != owner.id:
        raise NotFoundError("Project not found")
    return project


def create_prompt(db: Session, payload: PromptCreate) -> Prompt:
    project = _resolve_project(db, payload.project_id)
    prompt = Prompt(
        project_id=project.id,
        title=payload.title,
        idea=payload.idea,
        audience=payload.audience or "everyone",
        output_format=payload.output_format or "best",
        depth=payload.depth,
    )
    db.add(prompt)
    db.flush()
    if payload.body:
        db.add(
            PromptVersion(
                prompt_id=prompt.id,
                version_number=_next_version_number(db, prompt.id),
                body=payload.body,
            )
        )
        db.flush()
    db.commit()
    db.refresh(prompt)
    # Report the version just created, so the create response agrees with get_prompt and
    # the Studio can show a server-sourced version number for the very first save.
    _attach_latest_bodies(db, [prompt])
    return prompt


def update_prompt(db: Session, prompt_id: UUID, payload: PromptUpdate) -> Prompt:
    """Update a prompt; a supplied ``body`` APPENDS a new version.

    Two behaviors, one request:

    * **body supplied (non-empty)** — a NEW ``PromptVersion`` is appended with the next
      version number. Previous versions are left exactly as they were; the stored body
      is never overwritten in place.
    * **body omitted or empty** — pure metadata update, byte-for-byte the Phase 1
      behavior. A falsy body is treated as "no new version", which is the same
      convention ``create_prompt`` already applies to its own ``body`` field.

    Atomicity: the metadata change and the new version share a single ``db.commit()``,
    flushed beforehand, so either both persist or neither does. There is no second
    independent commit that could leave a half-applied update behind.
    """
    # Ownership-first and row-locked: nothing is read or written for a prompt this
    # caller does not own, and concurrent body edits cannot collide on a version number.
    prompt = _owned_prompt_for_update(db, prompt_id)
    data = payload.model_dump(exclude_unset=True)
    new_project_id = data.pop("project_id", None)
    if new_project_id is not None:
        prompt.project_id = _resolve_project(db, new_project_id).id
    # `body` is not a Prompt column: it must be consumed here instead of falling
    # through the setattr loop, which would only set a meaningless transient attribute.
    new_body = data.pop("body", None)
    for field, value in data.items():
        setattr(prompt, field, value)
    if new_body:
        db.add(
            PromptVersion(
                prompt_id=prompt.id,
                version_number=_next_version_number(db, prompt.id),
                body=new_body,
            )
        )
        db.flush()
    db.commit()
    db.refresh(prompt)
    # Report the newest version as the prompt body, so the response after a body update
    # carries the body that was just saved. Same helper get_prompt/list_prompts use.
    _attach_latest_bodies(db, [prompt])
    return prompt


def delete_prompt(db: Session, prompt_id: UUID) -> None:
    prompt = _owned_prompt(db, prompt_id)
    db.delete(prompt)
    db.commit()


def list_prompt_versions(db: Session, prompt_id: UUID) -> list[PromptVersion]:
    """Every stored version of an owned prompt, oldest first.

    Ownership is resolved before anything is read, so an unknown or foreign prompt
    fails with the identical 404 and no version data can leak. Ordering is by
    ``version_number`` with the row id as a deterministic tie-breaker — never
    ``created_at``. Reads take no row lock; only writers serialize.
    """
    prompt = _owned_prompt(db, prompt_id)
    return list(
        db.scalars(
            select(PromptVersion)
            .where(PromptVersion.prompt_id == prompt.id)
            .order_by(PromptVersion.version_number.asc(), PromptVersion.id.asc())
        )
    )


def restore_prompt_version(
    db: Session, prompt_id: UUID, version_id: UUID
) -> PromptVersion:
    """Restore a historical version by APPENDING a new version with its exact body.

    The source row is only ever read, never written: after this call the old version
    is byte-identical to before, and a new row carries the copied body under the next
    version number. The write reuses the Phase 3I machinery — the same row lock and
    the same ``_next_version_number`` helper — so a restore racing a body update or
    another restore still yields distinct, sequential numbers. One ``db.commit()``:
    either the new version exists or nothing changed.

    A version id that does not belong to this prompt — whether it exists elsewhere or
    nowhere — is a safe 404 that reveals nothing about any other prompt.
    """
    # Ownership-first and row-locked, exactly like the body-update path: nothing is
    # read or written for a prompt this caller does not own.
    prompt = _owned_prompt_for_update(db, prompt_id)
    source = db.scalar(
        select(PromptVersion).where(
            PromptVersion.id == version_id,
            PromptVersion.prompt_id == prompt.id,
        )
    )
    if source is None:
        raise NotFoundError("Prompt version not found")
    restored = PromptVersion(
        prompt_id=prompt.id,
        version_number=_next_version_number(db, prompt.id),
        body=source.body,
    )
    db.add(restored)
    db.flush()
    db.commit()
    db.refresh(restored)
    return restored