"""add checklist_public_share.name (named public links, chunk N1)

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-09

Adds the owner's short label for a public link, so several links on one card can
be told apart in the link list, in the send-by-email picker and (chunk N4) in the
"your link was opened" notification. See ``docs/plans/NAMED_PUBLIC_LINKS.md``.

The column is nullable in the database but never null in practice: create
resolves an empty name to the generated ``Link-<n>`` default, and the back-fill
below names every row that predates this revision. Numbering follows
``created_at`` per card, so the oldest link on a card becomes ``Link-1``.

**Idempotency (same trap as 0011 through 0017).** The server runs
``SQLModel.metadata.create_all`` (with ``checkfirst``) on *every* boot **before**
Alembic (see ``db/_init_db.py``), so on a fresh database the column already
exists by the time this revision runs and the ``add_column`` is skipped. The
back-fill only touches rows whose ``name IS NULL``, so re-running it is a no-op.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "checklist_public_share"
_COLUMN = "name"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table(_TABLE):
        # Nothing to add to and nothing to back-fill (create_all builds the table
        # from the model on the next boot, column included).
        return

    existing_columns = {c["name"] for c in insp.get_columns(_TABLE)}
    if _COLUMN not in existing_columns:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))

    # Back-fill: number each card's links by creation time and name the ones that
    # have no name yet. The window runs over *all* of a card's links, not just the
    # nameless ones, so a partial re-run cannot hand out a number that is already
    # taken. Links are few per card, so the write loop stays in Python rather than
    # carrying a separate UPDATE ... FROM statement per dialect.
    numbered = sa.text(
        """
        SELECT id, generated_name FROM (
            SELECT id,
                   name,
                   'Link-' || CAST(
                       ROW_NUMBER() OVER (
                           PARTITION BY checklist_id ORDER BY created_at, id
                       ) AS VARCHAR
                   ) AS generated_name
              FROM checklist_public_share
        ) AS numbered
        WHERE name IS NULL
        """
    )
    for row in bind.execute(numbered).fetchall():
        bind.execute(
            sa.text(
                "UPDATE checklist_public_share SET name = :name WHERE id = :id"
            ),
            {"name": row.generated_name, "id": row.id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table(_TABLE):
        return
    existing_columns = {c["name"] for c in insp.get_columns(_TABLE)}
    if _COLUMN in existing_columns:
        op.drop_column(_TABLE, _COLUMN)
