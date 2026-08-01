"""user notification settings

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-01

Adds ``user_notification_settings``, one lazily created row per user holding the
notification preference matrix (JSON), the user's time zone, their webhook target
(chunk E6) and the per-user secret that signs their unsubscribe links (chunk E4).
See ``docs/plans/EMAIL_NOTIFICATIONS.md`` section 3.1 and the model docstring in
``model/user_notification_settings.py``.

Purely additive: no existing table is touched, and no row is written for anybody.
A user without a row is served the instance defaults, so rolling back to the
previous release with this table in place changes nothing.

**Idempotency (same trap as 0012 and 0013).** The server runs
``SQLModel.metadata.create_all`` (with ``checkfirst``) on *every* boot **before**
Alembic (see ``db/_init_db.py``), so on an existing database being upgraded this
brand-new table and its index already exist by the time this revision runs. Every
DDL step is therefore guarded against the current state, which also makes the
revision safe to re-run.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "user_notification_settings"

# (index name, columns) exactly as the model declares them. The primary key
# (user_id) is indexed by the database itself, so it is not listed here.
_INDEXES = [
    (op.f("ix_user_notification_settings_server_seq"), ["server_seq"]),
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    had_table = insp.has_table(_TABLE)
    if not had_table:
        op.create_table(
            _TABLE,
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("prefs", sa.JSON(), nullable=False),
            sa.Column("timezone", sa.String(length=64), nullable=True),
            sa.Column("webhook_url", sa.String(length=2048), nullable=True),
            sa.Column("unsubscribe_secret", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("server_seq", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("user_id"),
        )

    # When the table pre-existed, `create_all` built its index too; reflect and
    # skip it. When we just created the table ourselves, the separate index
    # statement still has to run (and the inspector's cache predates it).
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
