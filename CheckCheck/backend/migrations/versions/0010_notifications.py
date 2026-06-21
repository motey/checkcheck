"""notification table + checklist_public_share.first_opened_at

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-21

Phase 9 of card sharing — in-app share notifications.

- notification: a persistent per-user feed entry (card_shared | card_invited |
  public_link_opened). ``user_id`` cascades on user delete; ``cl_id`` is a loose
  reference (no FK) so a notification survives the card it mentions being deleted.
- checklist_public_share.first_opened_at: set once on the first anonymous resolve
  of a link; the null->set transition triggers the one-time 'public_link_opened'
  notification to the link's creator.

``sa.Uuid()`` / ``sa.String()`` / ``sa.JSON()`` / ``sa.DateTime()`` match what
``SQLModel.metadata.create_all`` emits, so an upgraded DB and a fresh create_all
DB agree (see [[backend-test-harness]]).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("cl_id", sa.Uuid(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_id", "notification", ["id"])
    op.create_index("ix_notification_user_id", "notification", ["user_id"])

    op.add_column(
        "checklist_public_share",
        sa.Column("first_opened_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("checklist_public_share") as batch_op:
        batch_op.drop_column("first_opened_at")
    op.drop_index("ix_notification_user_id", "notification")
    op.drop_index("ix_notification_id", "notification")
    op.drop_table("notification")
