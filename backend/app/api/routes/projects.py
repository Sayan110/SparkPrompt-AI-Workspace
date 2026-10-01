from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.common import Message
from app.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate
from app.services import projects as projects_service

router = APIRouter()


@router.get("", response_model=list[ProjectRead])
def list_projects(db: Session = Depends(get_db)) -> list[ProjectRead]:
    return projects_service.list_projects(db)


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> ProjectRead:
    return projects_service.create_project(db, payload)


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: UUID, db: Session = Depends(get_db)) -> ProjectRead:
    return projects_service.get_project(db, project_id)


@router.put("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: UUID, payload: ProjectUpdate, db: Session = Depends(get_db)
) -> ProjectRead:
    return projects_service.update_project(db, project_id, payload)


@router.delete("/{project_id}", response_model=Message)
def delete_project(project_id: UUID, db: Session = Depends(get_db)) -> Message:
    projects_service.delete_project(db, project_id)
    return Message(message="Project deleted")