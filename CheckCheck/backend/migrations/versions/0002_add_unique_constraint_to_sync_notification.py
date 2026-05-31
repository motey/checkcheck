"""add unique constraint (cl_id, upd_prop) to sync_notification

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-31

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("syncnotification") as batch_op:
        batch_op.create_unique_constraint(
            "uq_sync_notification_cl_upd_prop", ["cl_id", "upd_prop"]
        )


def downgrade() -> None:
    with op.batch_alter_table("syncnotification") as batch_op:
        batch_op.drop_constraint(
            "uq_sync_notification_cl_upd_prop", type_="unique"
        )
