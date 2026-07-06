"""squashed baseline (pre-production)

Revision ID: 0010
Revises:
Create Date: 2026-07-06

Squash of the former revisions 0001-0010. There are no production instances
yet, so the incremental history was collapsed into this no-op baseline.

The revision id deliberately stays "0010" so existing dev databases (which are
stamped at the old head "0010") keep resolving. The schema itself is never
built by migrations: `init_schema_and_migrations()` creates it via
`SQLModel.metadata.create_all` on fresh databases and stamps head.

Until the first production release, schema changes are made in the SQLModel
models only — do NOT add Alembic revisions; recreate your dev database instead
(create_all does not alter existing tables). Dev databases stamped at a
pre-squash revision (0001-0009) must also be recreated.
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
