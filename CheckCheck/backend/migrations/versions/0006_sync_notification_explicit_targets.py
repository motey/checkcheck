"""add sync_notification.target_user_ids for explicit delivery targets

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-20

Lets a sync notification pin its recipients at emit time. Needed for events that
delete the rows target resolution relies on — deleting a checklist or
revoking/leaving a share — where resolving recipients from live DB state happens
*after* those rows are gone and would notify the wrong set (or nobody).

Nullable: existing/normal notifications leave it NULL and keep resolving targets
dynamically (owner + current collaborators).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "syncnotification",
        sa.Column("target_user_ids", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("syncnotification") as batch_op:
        batch_op.drop_column("target_user_ids")
