"""add prompt run version foreign key

Phase 3O declared ``prompt_runs.version_id -> prompt_versions.id`` on the
SQLAlchemy model but intentionally left the physical constraint out of the
database (runtime patching was additive-columns-only). Phase 4B makes the
model real:

- constraint: FOREIGN KEY (version_id) REFERENCES prompt_versions(id)
- on delete: CASCADE -- exactly what the PromptRun model declares
- nullability: unchanged (column stays NULLable)

Data rules enforced by this migration:
- Guarded: if any existing row references a missing prompt_version, the
  migration refuses to run. It never deletes or rewrites rows.
- Legacy runs with NULL version_id stay NULL. No body-based backfill: an
  identity link is never guessed from matching bodies.

REVERSIBLE: the downgrade drops only the constraint, never data.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    violations = connection.execute(
        sa.text(
            "SELECT count(*) FROM prompt_runs r "
            "LEFT JOIN prompt_versions v ON v.id = r.version_id "
            "WHERE r.version_id IS NOT NULL AND v.id IS NULL"
        )
    ).scalar()
    if violations:
        raise RuntimeError(
            f"Refusing to add prompt_runs.version_id foreign key: {violations} "
            "run row(s) reference a prompt_version that does not exist. Fix "
            "those references manually first; this migration never deletes "
            "or rewrites data."
        )
    op.create_foreign_key(
        "prompt_runs_version_id_fkey",
        "prompt_runs",
        "prompt_versions",
        ["version_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("prompt_runs_version_id_fkey", "prompt_runs", type_="foreignkey")
