from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Project, User

DEMO_EMAIL = "demo@sparkprompt.local"
DEFAULT_PROJECT_NAME = "Personal workspace"


def get_or_create_demo_user(db: Session) -> User:
    """Phase 1 runs without authentication, so all data is scoped to a single seeded demo user."""
    user = db.scalar(select(User).where(User.email == DEMO_EMAIL))
    if user is None:
        user = User(email=DEMO_EMAIL, display_name="Demo workspace")
        db.add(user)
        db.flush()
    return user


def get_or_create_default_project(db: Session, user: User) -> Project:
    project = db.scalar(
        select(Project).where(Project.user_id == user.id, Project.name == DEFAULT_PROJECT_NAME)
    )
    if project is None:
        project = Project(
            user_id=user.id, name=DEFAULT_PROJECT_NAME, description="Default home for prompts."
        )
        db.add(project)
        db.flush()
    return project


def ensure_demo_workspace(db: Session) -> None:
    user = get_or_create_demo_user(db)
    get_or_create_default_project(db, user)
    db.commit()