"""notification outbox

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-01

Adds ``notification_outbox``, the durable delivery queue behind the email (and
later webhook) channel: one row per (notification, channel) delivery, picked up
by the in-process dispatcher. See ``docs/plans/EMAIL_NOTIFICATIONS.md`` section
3.2 and the model docstring in ``model/notification_outbox.py``.

Purely additive: no existing table is touched, and nothing enqueues a row unless
the operator switches mail on. Rolling back to the previous release with this
table in place is harmless.

**Idempotency (same trap as 0012).** The server runs
``SQLModel.metadata.create_all`` (with ``checkfirst``) on *every* boot **before**
Alembic (see ``db/_init_db.py``), so on an existing database being upgraded this
brand-new table and its indexes already exist by the time this revision runs.
Every DDL step is therefore guarded against the current state, which also makes
the revision safe to re-run.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "notification_outbox"

# (index name, columns) exactly as the model declares them.
_INDEXES = [
    (op.f("ix_notification_outbox_id"), ["id"]),
    (op.f("ix_notification_outbox_user_id"), ["user_id"]),
    (op.f("ix_notification_outbox_notification_id"), ["notification_id"]),
    (op.f("ix_notification_outbox_dedupe_key"), ["dedupe_key"]),
    (op.f("ix_notification_outbox_server_seq"), ["server_seq"]),
    # The dispatcher's only hot query: "which rows are due now?".
    ("ix_notification_outbox_status_not_before", ["status", "not_before"]),
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    had_table = insp.has_table(_TABLE)
    if not had_table:
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("notification_id", sa.Uuid(), nullable=True),
            sa.Column("channel", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("not_before", sa.DateTime(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("last_error", sa.String(), nullable=True),
            sa.Column("dedupe_key", sa.String(), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("server_seq", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["notification_id"], ["notification.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("id"),
        )

    # When the table pre-existed, `create_all` built its indexes too; reflect and
    # skip those. When we just created the table ourselves, the separate index
    # statements still have to run (and the inspector's cache predates them).
    existing_indexes = (
        {ix["name"] for ix in insp.get_indexes(_TABLE)} if had_table else set()
    )
    for name, columns in _INDEXES:
        if name not in existing_indexes:
            op.create_index(name, _TABLE, columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table(_TABLE):
        return
    existing_indexes = {ix["name"] for ix in insp.get_indexes(_TABLE)}
    for name, _ in _INDEXES:
        if name in existing_indexes:
            op.drop_index(name, table_name=_TABLE)
    op.drop_table(_TABLE)
