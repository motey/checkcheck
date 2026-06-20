"""public URL share table + sync_notification.target_tokens

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-20

Phase 5 of card sharing — public/anonymous URL shares.

- checklist_public_share: a capability row whose ``token`` lets a logged-out
  visitor open one checklist at a ``view|check|edit`` level. Replaces the broken
  ``CheckListExternalShare`` scaffold (which was never wired into the schema, so
  there is nothing to drop here).
- syncnotification.target_tokens: like ``target_user_ids`` but addresses connected
  *anonymous* SSE clients by their public-share token, so live updates reach
  logged-out viewers (and their writes still reach owner + collaborators).

``sa.Uuid()`` matches what ``SQLModel.metadata.create_all`` emits (CHAR(32) on
SQLite, UUID on Postgres), so an upgraded DB and a fresh create_all DB agree.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "checklist_public_share",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("checklist_id", sa.Uuid(), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("permission", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["checklist_id"], ["checklist.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_checklist_public_share_id", "checklist_public_share", ["id"]
    )
    op.create_index(
        "ix_checklist_public_share_checklist_id",
        "checklist_public_share",
        ["checklist_id"],
    )
    op.create_index(
        "ix_checklist_public_share_token",
        "checklist_public_share",
        ["token"],
        unique=True,
    )

    op.add_column(
        "syncnotification",
        sa.Column("target_tokens", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("syncnotification") as batch_op:
        batch_op.drop_column("target_tokens")
    op.drop_index("ix_checklist_public_share_token", "checklist_public_share")
    op.drop_index(
        "ix_checklist_public_share_checklist_id", "checklist_public_share"
    )
    op.drop_index("ix_checklist_public_share_id", "checklist_public_share")
    op.drop_table("checklist_public_share")
