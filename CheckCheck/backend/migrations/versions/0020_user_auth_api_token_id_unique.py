"""unique index on user_auth.api_token_id (API token review, chunk 4)

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-18

Every request made with an API key looks the key up by ``api_token_id``. Without
an index that lookup scans the whole ``user_auth`` table, and a duplicate value
would turn the lookup into a 500 (``one_or_none``). See ``TOKEN_API_REVIEW.md``,
finding 9. ``api_token_id`` is NULL for every non-token login; both Postgres and
SQLite allow any number of NULLs in a unique index.

Duplicates are practically impossible (96 random bits per id), but a unique index
cannot be built over them. The upgrade checks first and stops with a message that
names the duplicate ids instead of failing inside ``CREATE INDEX``.

**Idempotency (same trap as 0011 through 0019).** The server runs
``SQLModel.metadata.create_all`` (with ``checkfirst``) on *every* boot **before**
Alembic (see ``db/_init_db.py``). A fresh database therefore gets the index from
the model, and the ``create_index`` below is skipped when it already exists.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "user_auth"
_COLUMN = "api_token_id"
_INDEX = "ix_user_auth_api_token_id"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table(_TABLE):
        # create_all builds the table from the model on the next boot.
        return
    if any(ix["name"] == _INDEX for ix in insp.get_indexes(_TABLE)):
        return

    duplicates = bind.execute(
        sa.text(
            """
            SELECT api_token_id, COUNT(*) AS n
              FROM user_auth
             WHERE api_token_id IS NOT NULL
             GROUP BY api_token_id
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()
    if duplicates:
        listed = ", ".join(f"{row.api_token_id!r} ({row.n} rows)" for row in duplicates)
        raise RuntimeError(
            f"Cannot create the unique index {_INDEX}: user_auth.api_token_id has "
            f"duplicate values: {listed}. These API keys are ambiguous and fail on "
            "every use. Delete the affected user_auth rows (the owners have to "
            "create new keys) and start the server again."
        )

    op.create_index(_INDEX, _TABLE, [_COLUMN], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table(_TABLE):
        return
    if any(ix["name"] == _INDEX for ix in insp.get_indexes(_TABLE)):
        op.drop_index(_INDEX, table_name=_TABLE)
