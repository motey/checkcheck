"""instance secret (generated VAPID keys, chunk K1)

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-04

Adds ``instance_secret``, one row per secret this instance generated for
itself. Its first tenant is the VAPID key pair the push channel signs with, so
that push works on an instance nobody hand-configured. See
``docs/plans/PUSH_KEYS_AND_SHARE_SETTING.md`` chunk K1 and the model docstring
in ``model/instance_secret.py``.

Purely additive: no existing table is touched and no row is written here (the
key pair is generated on the first boot that needs it, not by this migration).
A release without the push-key code simply never reads this table, so rolling
back to it with the table in place changes nothing.

**Idempotency (same trap as 0012 through 0016).** The server runs
``SQLModel.metadata.create_all`` (with ``checkfirst``) on *every* boot
**before** Alembic (see ``db/_init_db.py``), so on an existing database being
upgraded this brand-new table and its index already exist by the time this
revision runs. Every DDL step is therefore guarded against the current state,
which also makes the revision safe to re-run.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "instance_secret"

# (index name, columns, unique) exactly as the model declares them. Only
# ``server_seq`` (inherited from ``TimestampedModel``) is indexed: ``name`` is
# the primary key and gets the database's own index, and unlike the id columns
# of 0015/0016 it is not additionally declared ``index=True``, so ``create_all``
# emits no second index for it.
_INDEXES = [
    (op.f("ix_instance_secret_server_seq"), ["server_seq"], False),
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
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("value", sa.String(length=2048), nullable=False),
            sa.PrimaryKeyConstraint("name"),
        )

    # When the table pre-existed, `create_all` built its index too; reflect and
    # skip it. When we just created the table ourselves, the separate index
    # statement still has to run (and the inspector's cache predates it).
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
