"""add display_name and last_used_at to user_auth

Revision ID: 0001
Revises:
Create Date: 2026-05-29

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_auth",
        sa.Column("display_name", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "user_auth",
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # SQLite does not support DROP COLUMN in older versions; use batch mode if needed.
    with op.batch_alter_table("user_auth") as batch_op:
        batch_op.drop_column("last_used_at")
        batch_op.drop_column("display_name")
