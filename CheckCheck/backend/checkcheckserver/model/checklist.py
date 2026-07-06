from typing import AsyncGenerator, List, Optional, Literal, Sequence, Annotated, Dict
import enum
from pydantic import (
    validate_email,
    StringConstraints,
    field_validator,
    ValidationInfo,
)
import datetime
from fastapi import Depends
from typing import Optional
from sqlmodel import Field, UniqueConstraint, Relationship

import uuid
from uuid import UUID

from checkcheckserver.model._base_model import BaseTable, TimestampedModel
from checkcheckserver.model.checklist_color_scheme import ChecklistColorScheme
from checkcheckserver.model.checklist_position import (
    CheckListPosition,
    CheckListPositionApiCreate,
    CheckListPositionPublicWithoutChecklistID,
)
from checkcheckserver.model.label import Label, LabelReadAPI
from checkcheckserver.model.checklist_label import CheckListLabel
from checkcheckserver.model.user import User
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


log = get_logger()
config = Config()


class SharedFilter(str, enum.Enum):
    """Sharing-based list filter for the checklist grid. Mutually exclusive with
    itself; ANDs with label/search/archived filters.

    ``with_me`` — cards owned by someone else that the caller accepted a share on.
    ``by_me``   — cards the caller owns that have at least one accepted collaborator.
    """

    with_me = "with_me"
    by_me = "by_me"


class CheckListBase(BaseTable):
    name: Optional[str] = Field(
        description="The display name of the list", default_factory=str
    )
    text: Optional[str] = Field(
        description="A text that will be shown at the header", default_factory=str
    )
    color_id: Optional[str] = Field(
        default=None, foreign_key="checklist_color_scheme.id"
    )
    checked_items_seperated: Optional[bool] = Field(default=True)
    checked_items_collapsed: Optional[bool] = Field(
        default=True,
        description="If checked_items_seperated is enabled and this is set to true the client should hide any checked items. ",
    )


class CheckList(CheckListBase, TimestampedModel, table=True):
    __tablename__ = "checklist"
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
        unique=True,
        # sa_column_kwargs={"server_default": text("gen_random_uuid()")},
    )
    color_id: Optional[str] = Field(
        foreign_key="checklist_color_scheme.id",
        default=None,
    )
    color: Optional[ChecklistColorScheme] = Relationship(
        sa_relationship_kwargs={"lazy": "joined"}
    )
    owner_id: uuid.UUID = Field(foreign_key="user.id")
    owner: User = Relationship(sa_relationship_kwargs={"lazy": "joined"})
    position: CheckListPosition = Relationship(
        cascade_delete=True,
        back_populates="checklist",
        sa_relationship_kwargs={"lazy": "joined"},
    )
    labels: List[Label] = Relationship(
        cascade_delete=False,
        link_model=CheckListLabel,
        sa_relationship_kwargs={"lazy": "joined"},
    )


class CheckListUpdate(CheckListBase):
    name: Optional[str] = Field(
        default=None, description="The display name of the list"
    )
    text: Optional[str] = Field(
        default=None, description="A text that will be shown at the header"
    )
    color_id: Optional[str] = Field(
        default=None, foreign_key="checklist_color_scheme.id"
    )


class CheckListCreate(CheckListBase):
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
        unique=True,
    )
    owner_id: uuid.UUID = Field(foreign_key="user.id")


class CheckListApi(CheckListBase):
    id: uuid.UUID
    updated_at: datetime.datetime


class CheckListApiCreate(CheckListBase):
    position: CheckListPositionApiCreate | None = None


class CheckListCountsPublic(BaseTable):
    """Aggregate card counts for the sidebar badges (one request, no N+1). Every
    count is access-scoped to the caller and, except ``archived``, excludes
    archived cards. ``labels`` maps each of the caller's label ids to the number
    of its non-archived cards (labels with no cards are omitted)."""

    home: int
    shared_with_me: int
    shared_by_me: int
    archived: int
    labels: Dict[uuid.UUID, int]


class CheckListApiWithSubObj(CheckListApi):
    owner_id: uuid.UUID
    # The caller's effective permission on this card, on the
    # view < check < edit < owner ladder (see ChecklistAccessLevel). This is the
    # single source of truth the client gates owner-only / collaborator UI on. It
    # is not a stored column — every route returning this model attaches it for the
    # current caller (owner -> "owner"; for an anonymous public read, the link's
    # level).
    my_permission: Literal["view", "check", "edit", "owner"]
    color: Optional[ChecklistColorScheme]
    position: CheckListPositionPublicWithoutChecklistID
    labels: list[LabelReadAPI]
