from typing import AsyncGenerator, List, Optional, Literal, Sequence, Annotated
from pydantic import (
    validate_email,
    validator,
    StringConstraints,
    field_validator,
    ValidationInfo,
    PositiveInt,
    NonNegativeInt,
)
import datetime
from fastapi import Depends
from typing import Optional
from sqlmodel import Field, UniqueConstraint, Relationship, ForeignKeyConstraint

import uuid
from uuid import UUID

from checkcheckserver.model._base_model import BaseTable, TimestampedModel
from checkcheckserver.model.checklist_color_scheme import ChecklistColorScheme
from checkcheckserver.model.checklist_item_state import (
    CheckListItemState,
    CheckListItemStateApiCreate,
    CheckListItemStateCreate,
    CheckListItemStateWithoutChecklistID,
)
from checkcheckserver.model.checklist_item_position import (
    CheckListItemPosition,
    CheckListItemPositionCreate,
    CheckListItemPositionApiCreate,
    CheckListItemPositionPublicWithoutChecklistID,
)


from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


log = get_logger()
config = Config()


class CheckListItemBase(BaseTable, table=False):
    text: Optional[str] = Field(
        description="The display name of the list", default_factory=str
    )


class CheckListItemCreate(CheckListItemBase, table=False):
    id: Optional[uuid.UUID] = Field(default_factory=uuid.uuid4)
    checklist_id: uuid.UUID = Field(exclude=True)


class CheckListItemUpdate(CheckListItemBase, table=False):
    text: Optional[str] = Field(
        description="The display name of the list", default_factory=str
    )


class CheckListItem(CheckListItemCreate, table=True):
    __tablename__ = "checklist_item"
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
        unique=True,
        # sa_column_kwargs={"server_default": text("gen_random_uuid()")},
    )
    text: str = Field(description="The display name of the list")
    checklist_id: uuid.UUID = Field(foreign_key="checklist.id", exclude=True)
    position: CheckListItemPosition = Relationship(
        back_populates="checklist_item",
        cascade_delete=True,
        sa_relationship_kwargs={"lazy": "joined"},
    )
    state: CheckListItemState = Relationship(
        back_populates="checklist_item",
        cascade_delete=True,
        sa_relationship_kwargs={"lazy": "joined"},
    )


class CheckListItemRead(CheckListItemCreate):
    id: uuid.UUID
    position: CheckListItemPositionPublicWithoutChecklistID
    state: CheckListItemStateWithoutChecklistID


class CheckListItemCreateAPI(CheckListItemBase, table=False):
    text: Optional[str] = Field(
        description="The display name of the list", default_factory=str
    )
    position: Optional[CheckListItemPositionApiCreate] = Field(default=None)
    state: Optional[CheckListItemStateApiCreate] = Field(default=None)
