from typing import AsyncGenerator, List, Optional, Literal, Sequence, Annotated
import enum
from pydantic import (
    validate_email,
    validator,
    StringConstraints,
    field_validator,
    ValidationInfo,
)
import datetime
from fastapi import Depends
from typing import Optional
from sqlmodel import Field, UniqueConstraint, String

import uuid
from uuid import UUID

from checkcheckserver.model._base_model import BaseTable, TimestampedModel
from checkcheckserver.model.checklist_color_scheme import ChecklistColorScheme

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


log = get_logger()
config = Config()


class SharePermission(str, enum.Enum):
    """A permission level that can be *granted* to a share target (collaborator or
    public link). Ownership is not grantable via a share — it is transferred via
    the dedicated transfer-ownership action — so it is intentionally absent here.
    See ``checkcheckserver.api.access.ChecklistAccessLevel`` for the full ladder
    (which adds ``owner``) used for authorization checks."""

    view = "view"  # read only
    check = "check"  # toggle item checked-state, but no content edits
    edit = "edit"  # full edit of the card and its items


class ShareStatus(str, enum.Enum):
    """Lifecycle of a share grant. With ``SHARING_REQUIRE_INVITE_ACCEPT`` off
    (default) every share is created ``accepted`` and behaves as it always has.
    With the flag on, a fresh share starts ``pending`` and grants **no** access
    until the target accepts; declining keeps a ``declined`` row (so the owner can
    re-invite and the UI can show "you previously declined")."""

    pending = "pending"  # invited, awaiting the target's decision — no access yet
    accepted = "accepted"  # live access
    declined = "declined"  # the target turned the invite down — no access


class CheckListCollaboratorCreate(TimestampedModel, table=False):
    checklist_id: uuid.UUID = Field(
        foreign_key="checklist.id", primary_key=True, ondelete="CASCADE"
    )
    user_id: uuid.UUID = Field(foreign_key="user.id", primary_key=True)
    permission: SharePermission = Field(
        default=SharePermission.edit,
        sa_type=String,
        description="What the collaborator is allowed to do. Defaults to 'edit' to preserve the behavior of collaborators created before permission levels existed.",
    )
    status: ShareStatus = Field(
        default=ShareStatus.accepted,
        sa_type=String,
        description="Whether this share is a live grant ('accepted'), an unaccepted invite ('pending'), or was turned down ('declined'). Defaults to 'accepted' so shares created when SHARING_REQUIRE_INVITE_ACCEPT is off — and pre-existing rows — grant access immediately.",
    )
    via_group: Optional[str] = Field(
        default=None,
        sa_type=String,
        description=(
            "Provenance marker for living group shares. NULL means this is an "
            "explicit individual share (the historical behavior, and every "
            "pre-existing row) — the group-share reconciler never touches it. A "
            "non-NULL value means the row was materialized from that OIDC group "
            "share; the reconciler owns such rows (recomputing the level across "
            "all matching group shares, and removing the row when the user no "
            "longer qualifies). Explicit shares always win: a member holding an "
            "explicit row is skipped by group materialization."
        ),
    )


class CheckListCollaborator(CheckListCollaboratorCreate, table=True):
    __tablename__ = "checklist_collaborator"
