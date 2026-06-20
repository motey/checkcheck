"""checklist_public_share.password_hash

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-20

Phase 7 of card sharing — password-protected public links.

Adds a nullable ``password_hash`` to ``checklist_public_share``. Existing links
backfill to ``NULL`` (unprotected), preserving today's behaviour. The plaintext
passphrase is never stored — only its bcrypt hash, set via the share-management
API. ``sa.String()`` matches what ``SQLModel.metadata.create_all`` emits for an
``Optional[str]`` column, so an upgraded DB and a fresh create_all DB agree.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "checklist_public_share",
        sa.Column("password_hash", sa.String(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("checklist_public_share") as batch_op:
        batch_op.drop_column("password_hash")
