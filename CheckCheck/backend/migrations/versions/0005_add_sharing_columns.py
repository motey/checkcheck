"""add sharing columns: user.oidc_groups and checklist_collaborator.permission

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-18

Phase 1 of the card-sharing feature.

- user.oidc_groups: JSON list of the OIDC groups the user belonged to on their
  last OIDC login. Used to optionally restrict user search to shared groups.
- checklist_collaborator.permission: the share level ('view' | 'check' | 'edit').
  Existing collaborator rows are backfilled to 'edit' so their behavior is
  unchanged (collaborators previously had full edit access).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("oidc_groups", sa.JSON(), nullable=True),
    )
    op.execute("UPDATE \"user\" SET oidc_groups = '[]' WHERE oidc_groups IS NULL")

    op.add_column(
        "checklist_collaborator",
        sa.Column("permission", sa.String(), nullable=True),
    )
    op.execute(
        "UPDATE checklist_collaborator SET permission = 'edit' WHERE permission IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("checklist_collaborator") as batch_op:
        batch_op.drop_column("permission")
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("oidc_groups")
