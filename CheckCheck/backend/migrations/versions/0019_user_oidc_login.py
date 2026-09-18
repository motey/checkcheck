"""add user.oidc_provider_slug and user.last_oidc_login_at (API token review, chunk 2)

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-18

Both are set by the OIDC callback on every OIDC login. They let API keys follow
the user's provider instead of the credential of the request (see
``TOKEN_API_REVIEW.md``, findings 1 and 4):

* ``oidc_provider_slug`` decides ``RESTRICT_USER_SEARCH_TO_OWN_GROUPS`` for the
  user, so a key of a restricted user is restricted as well.
* ``last_oidc_login_at`` pauses a user's API keys when their last OIDC login is
  older than ``API_TOKEN_MANAGEMENT_OIDC_LOGIN_MAX_AGE_DAYS``.

Back-fill: ``oidc_provider_slug`` is copied from the user's newest OIDC
``user_auth`` row, so the group restriction for keys applies right after the
upgrade to every user who still has an OIDC login stored. ``last_oidc_login_at``
deliberately stays NULL: filling it from old logins would pause keys on upgrade.
Existing OIDC users are exempt from the pause until their next OIDC login.

**Idempotency (same trap as 0011 through 0018).** The server runs
``SQLModel.metadata.create_all`` (with ``checkfirst``) on *every* boot **before**
Alembic (see ``db/_init_db.py``), so on a fresh database the columns already
exist by the time this revision runs and the ``add_column`` calls are skipped.
The back-fill only touches rows whose ``oidc_provider_slug IS NULL``.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "user"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table(_TABLE):
        # create_all builds the table from the model on the next boot.
        return

    existing_columns = {c["name"] for c in insp.get_columns(_TABLE)}
    if "oidc_provider_slug" not in existing_columns:
        op.add_column(
            _TABLE,
            sa.Column("oidc_provider_slug", sa.String(length=128), nullable=True),
        )
    if "last_oidc_login_at" not in existing_columns:
        op.add_column(
            _TABLE, sa.Column("last_oidc_login_at", sa.DateTime(), nullable=True)
        )

    if not insp.has_table("user_auth"):
        return
    # Newest OIDC login per user. Few rows per user, so the write loop stays in
    # Python rather than a dialect-specific UPDATE ... FROM.
    newest_oidc_login = sa.text(
        """
        SELECT user_id, oidc_provider_slug FROM (
            SELECT ua.user_id,
                   ua.oidc_provider_slug,
                   ROW_NUMBER() OVER (
                       PARTITION BY ua.user_id ORDER BY ua.created_at DESC
                   ) AS rn
              FROM user_auth ua
              JOIN "user" u ON u.id = ua.user_id
             WHERE CAST(ua.auth_source_type AS VARCHAR) = 'oidc'
               AND ua.oidc_provider_slug IS NOT NULL
               AND u.oidc_provider_slug IS NULL
        ) AS newest
        WHERE rn = 1
        """
    )
    for row in bind.execute(newest_oidc_login).fetchall():
        bind.execute(
            sa.text(
                'UPDATE "user" SET oidc_provider_slug = :slug WHERE id = :id'
            ),
            {"slug": row.oidc_provider_slug, "id": row.user_id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table(_TABLE):
        return
    existing_columns = {c["name"] for c in insp.get_columns(_TABLE)}
    for column in ("last_oidc_login_at", "oidc_provider_slug"):
        if column in existing_columns:
            op.drop_column(_TABLE, column)
