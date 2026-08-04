"""scheduled notification (date-based reminders)

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-02

Adds ``scheduled_notification``, one row per reminder a user set on a card. The
dispatcher's per-tick scan picks up due rows and emits a ``reminder_due``
notification through the existing seam. See ``docs/plans/DATE_REMINDERS.md``
section 3.1 and the model docstring in ``model/scheduled_notification.py``.

Purely additive: no existing table is touched and no row is written for anybody.
A release without the reminder code simply never reads this table, so rolling
back to it with the table in place changes nothing.

**Idempotency (same trap as 0012, 0013 and 0014).** The server runs
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
revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "scheduled_notification"

# (index name, columns, unique) exactly as the model declares them. The primary
# key is indexed by the database itself; ``ix_scheduled_notification_id`` is the
# separate unique index SQLModel emits for a ``Field(index=True, unique=True)``
# id, and it is listed because ``create_all`` builds it too.
_INDEXES = [
    (op.f("ix_scheduled_notification_id"), ["id"], True),
    (op.f("ix_scheduled_notification_server_seq"), ["server_seq"], False),
    (op.f("ix_scheduled_notification_user_id"), ["user_id"], False),
    (op.f("ix_scheduled_notification_cl_id"), ["cl_id"], False),
    # The scan's only hot query: "which pending rows are due now?".
    ("ix_scheduled_notification_status_remind_at", ["status", "remind_at"], False),
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    had_table = insp.has_table(_TABLE)
    if not had_table:
        op.create_table(
            _TABLE,
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("server_seq", sa.Integer(), nullable=True),
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("cl_id", sa.Uuid(), nullable=False),
            sa.Column("remind_at", sa.DateTime(), nullable=False),
            sa.Column("timezone", sa.String(length=64), nullable=True),
            sa.Column("recurrence", sa.String(), nullable=False),
            sa.Column("note", sa.String(length=200), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("last_fired_at", sa.DateTime(), nullable=True),
            sa.Column("fire_count", sa.Integer(), nullable=False),
            sa.Column("anchor_day", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["cl_id"], ["checklist.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    # When the table pre-existed, `create_all` built its indexes too; reflect and
    # skip them. When we just created the table ourselves, the separate index
    # statements still have to run (and the inspector's cache predates them).
    existing_indexes = (
        {ix["name"] for ix in insp.get_indexes(_TABLE)} if had_table else set()
    )
    for name, columns, unique in _INDEXES:
        if name not in existing_indexes:
            op.create_index(name, _TABLE, columns, unique=unique)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table(_TABLE):
        return
    existing_indexes = {ix["name"] for ix in insp.get_indexes(_TABLE)}
    for name, _columns, _unique in _INDEXES:
        if name in existing_indexes:
            op.drop_index(name, table_name=_TABLE)
    op.drop_table(_TABLE)
