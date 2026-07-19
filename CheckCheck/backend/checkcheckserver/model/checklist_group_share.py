"""First-class group share model (living group membership).

A ``CheckListGroupShare`` records that a checklist is shared with an OIDC *group*
at a permission level. Unlike the original one-shot group expansion (which wrote
per-member ``CheckListCollaborator`` rows and remembered nothing), this row is the
**source of truth**: it persists so the owner can see the shared groups, revoke a
group as a unit, and — because membership is reconciled at login and on
create/revoke — so members who join or leave the group gain or lose access.

The row itself is a *source/config* table: it is deliberately NOT read by the
delta feed. Access changes reach clients through the ``CheckListCollaborator`` +
``CheckListPosition`` rows the reconciler materializes for group members, which
already participate in the sync machinery. It inherits ``TimestampedModel`` for
the naive-UTC ``created_at``/``updated_at`` convention (its ``server_seq`` column
is stamped like any other row but never queried — harmless).

See ``api/group_share_reconcile.py`` for the materialize/reconcile logic and
``docs/plans/GROUP_SHARE_LIVING_MEMBERSHIP.md`` for the full design.
"""

import uuid

from sqlmodel import Field, String

from checkcheckserver.model._base_model import TimestampedModel
from checkcheckserver.model.checklist_collaborator import SharePermission

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


log = get_logger()
config = Config()


class CheckListGroupShareCreate(TimestampedModel, table=False):
    checklist_id: uuid.UUID = Field(
        foreign_key="checklist.id", primary_key=True, ondelete="CASCADE"
    )
    # The OIDC group name, half of the composite primary key: a checklist may be
    # shared with many groups, but each group at most once (upsert raises/lowers
    # the level in place).
    group: str = Field(primary_key=True, sa_type=String)
    permission: SharePermission = Field(
        default=SharePermission.edit,
        sa_type=String,
        description="Permission level granted to every current member of the group.",
    )
    created_by: uuid.UUID = Field(
        foreign_key="user.id",
        description="The owner who shared the card with this group.",
    )


class CheckListGroupShare(CheckListGroupShareCreate, table=True):
    __tablename__ = "checklist_group_share"
