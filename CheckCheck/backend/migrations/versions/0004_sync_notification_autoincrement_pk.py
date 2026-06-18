"""replace sync_notification float-timestamp PK with autoincrement id

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-18

The float `timestamp` column was the primary key. Two notifications created
within the resolution of time.time() collide on the PK, raising IntegrityError
and failing the originating API write — which happens precisely during the
rapid/bulk operations that produce many notifications at once.

This swaps to an autoincrement integer `id` primary key and keeps `timestamp`
as an ordinary indexed column for ordering. The table is a transient drain
queue (rows are deleted as they are delivered, and the Postgres backend doesn't
use it at all), so dropping and recreating it loses nothing of value.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The table only ever holds undelivered queue rows, so a clean recreate is
    # the simplest correct way to change the primary key on SQLite.
    op.drop_table("syncnotification")
    op.create_table(
        "syncnotification",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("timestamp", sa.Float(), nullable=False),
        sa.Column("cl_id", sa.Uuid(), nullable=False),
        sa.Column("cli_id", sa.Uuid(), nullable=True),
        sa.Column("upd_prop", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_syncnotification_timestamp", "syncnotification", ["timestamp"]
    )


def downgrade() -> None:
    op.drop_index("ix_syncnotification_timestamp", table_name="syncnotification")
    op.drop_table("syncnotification")
    op.create_table(
        "syncnotification",
        sa.Column("timestamp", sa.Float(), nullable=False),
        sa.Column("cl_id", sa.Uuid(), nullable=False),
        sa.Column("cli_id", sa.Uuid(), nullable=True),
        sa.Column("upd_prop", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("timestamp"),
    )
