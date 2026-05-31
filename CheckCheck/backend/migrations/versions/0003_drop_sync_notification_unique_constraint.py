"""drop overly-broad unique constraint from sync_notification

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-31

The (cl_id, upd_prop) unique constraint was added to deduplicate notifications
but is semantically wrong: two different items can legitimately produce the same
upd_prop (e.g. "item_position") for the same checklist. The second insert would
silently overwrite the first item's cli_id, losing the update. Deduplication is
now handled by the frontend debounce instead.
"""

from typing import Sequence, Union
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    try:
        with op.batch_alter_table("syncnotification") as batch_op:
            batch_op.drop_constraint(
                "uq_sync_notification_cl_upd_prop", type_="unique"
            )
    except (ValueError, KeyError):
        pass  # constraint absent on fresh databases — nothing to drop


def downgrade() -> None:
    with op.batch_alter_table("syncnotification") as batch_op:
        batch_op.create_unique_constraint(
            "uq_sync_notification_cl_upd_prop", ["cl_id", "upd_prop"]
        )
