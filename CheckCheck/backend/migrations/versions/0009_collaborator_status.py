"""checklist_collaborator.status

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-21

Phase 8 of card sharing — invite / accept flow.

Adds a nullable-then-backfilled ``status`` column to ``checklist_collaborator``
('pending' | 'accepted' | 'declined'). Every existing collaborator row already
has live access, so it backfills to ``accepted`` — they must not regress to
``pending`` (which grants no access). ``sa.String()`` matches what
``SQLModel.metadata.create_all`` emits for the enum-as-string column, so an
upgraded DB and a fresh create_all DB agree.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "checklist_collaborator",
        sa.Column("status", sa.String(), nullable=True),
    )
    op.execute(
        "UPDATE checklist_collaborator SET status = 'accepted' WHERE status IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("checklist_collaborator") as batch_op:
        batch_op.drop_column("status")
