from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Project
from app.schemas.project import ProjectCreate, ProjectUpdate
from app.services.errors import NotFoundError
from app.services.identity import resolve_owner


def list_projects(db: Session) -> list[Project]:
    owner = resolve_owner(db)
    return list(
        db.scalars(
            select(Project)
            .where(Project.user_id == owner.id)
            .order_by(Project.created_at.desc())
        )
    )


def _owned_project(db: Session, project_id: UUID) -> Project:
    owner = resolve_owner(db)
    project = db.get(Project, project_id)
    if project is None or project.user_id != owner.id:
        raise NotFoundError("Project not found")
    return project


def get_project(db: Session, project_id: UUID) -> Project:
    return _owned_project(db, project_id)


def create_project(db: Session, payload: ProjectCreate) -> Project:
    owner = resolve_owner(db)
    project = Project(user_id=owner.id, name=payload.name, description=payload.description)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def update_project(db: Session, project_id: UUID, payload: ProjectUpdate) -> Project:
    project = _owned_project(db, project_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


def delete_project(db: Session, project_id: UUID) -> None:
    project = _owned_project(db, project_id)
    db.delete(project)
    db.commit()