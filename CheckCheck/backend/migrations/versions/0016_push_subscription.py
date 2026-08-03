"""push subscription (Web Push, chunk P1 of system notifications)

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-03

Adds ``push_subscription``, one row per browser or installed-PWA device
subscribed to Web Push. See ``docs/plans/SYSTEM_NOTIFICATIONS.md`` section 3.1
and the model docstring in ``model/push_subscription.py``.

Purely additive: no existing table is touched and no row is written for
anybody. A release without the push code simply never reads this table, so
rolling back to it with the table in place changes nothing.

**Idempotency (same trap as 0012 through 0015).** The server runs
``SQLModel.metadata.create_all`` (with ``checkfirst``) on *every* boot
**before** Alembic (see ``db/_init_db.py``), so on an existing database being
upgraded this brand-new table and its indexes already exist by the time this
revision runs. Every DDL step is therefore guarded against the current state,
which also makes the revision safe to re-run.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "push_subscription"

# (index name, columns, unique) exactly as the model declares them. The
# primary key (id) is indexed by the database itself, but SQLModel also emits
# its own separate unique index for a `Field(index=True, unique=True)` id
# (`ix_push_subscription_id`), and `create_all` builds that one too, so it is
# listed like the equivalent id index in 0015.
_INDEXES = [
    (op.f("ix_push_subscription_id"), ["id"], True),
    (op.f("ix_push_subscription_server_seq"), ["server_seq"], False),
    (op.f("ix_push_subscription_user_id"), ["user_id"], False),
    # What makes re-subscribing the same browser an upsert rather than a
    # growing pile of duplicates pushing to the same, now-redundant, target.
    (op.f("ix_push_subscription_endpoint"), ["endpoint"], True),
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
            sa.Column("endpoint", sa.String(length=1024), nullable=False),
            sa.Column("p256dh", sa.String(length=255), nullable=False),
            sa.Column("auth", sa.String(length=255), nullable=False),
            sa.Column("user_agent", sa.String(length=512), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    # When the table pre-existed, `create_all` built its indexes too; reflect
    # and skip them. When we just created the table ourselves, the separate
    # index statements still have to run (and the inspector's cache predates
    # them).
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
