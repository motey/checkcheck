from typing import (
    AsyncGenerator,
    List,
    Optional,
    Literal,
    Sequence,
    Annotated,
    TYPE_CHECKING,
)
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

from checkcheckserver.model._base_model import TimestampedModel, BaseTable

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger

if TYPE_CHECKING:
    from checkcheckserver.model.checklist import CheckList

log = get_logger()
config = Config()

"""
class CheckListPositionBase(BaseTable, table=False):
    index: Optional[float] = Field(default=None)
    pinned: Optional[int] = Field(default=None)
    archived: Optional[bool] = Field(default=None)
    checked_items_collapsed: Optional[bool] = Field(default=False)
"""


class CheckListPositionApiCreate(BaseTable, table=False):
    index: Optional[float] = Field(
        default=None,
        description="The position of a checklist relative to other checklist 'index' value. The lowest value should be the first checklist in a client representation.",
    )
    pinned: Optional[int] = Field(
        default=False,
        description="Pinned items should get a more prominent placement in the client.",
    )
    archived: Optional[bool] = Field(
        default=False,
        description="Archived items are filtered out by default. They are considered obsolete.",
    )

    checked_items_seperated: Optional[bool] = Field(default=True)
    checked_items_collapsed: Optional[bool] = Field(
        default=True,
        description="If checked_items_seperated is enabled and this is set to true the client should hide any checked items. ",
    )


class CheckListPositionUpdate(CheckListPositionApiCreate, table=False):
    checked_items_seperated: Optional[bool] = Field(default=None)


class CheckListPositionCreate(CheckListPositionUpdate, table=False):
    checklist_id: uuid.UUID = Field()
    user_id: uuid.UUID = Field()
    index: float = Field(
        description="The position of a checklist relative to other checklist 'index' value. The lowest value should be the first checklist in a client representation."
    )


class CheckListPosition(CheckListPositionCreate, table=True):
    __tablename__ = "checklist_position"
    checklist_id: uuid.UUID = Field(foreign_key="checklist.id", primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", primary_key=True, exclude=True)
    checklist: "CheckList" = Relationship(
        back_populates="position", sa_relationship_kwargs={"lazy": "joined"}
    )


class CheckListPositionPublicWithoutChecklistID(CheckListPositionCreate, table=False):
    checklist_id: uuid.UUID = Field(
        foreign_key="checklist.id", primary_key=True, exclude=True
    )
    user_id: uuid.UUID = Field(foreign_key="user.id", primary_key=True, exclude=True)
    checklist: "CheckList" = Relationship(
        back_populates="position", sa_relationship_kwargs={"lazy": "joined"}
    )
