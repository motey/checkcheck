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


class CheckListCollaborator(CheckListCollaboratorCreate, table=True):
    __tablename__ = "checklist_collaborator"
