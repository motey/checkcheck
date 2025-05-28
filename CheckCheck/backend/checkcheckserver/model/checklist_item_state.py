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
from sqlmodel import Field, UniqueConstraint, Relationship, SQLModel

import uuid
from uuid import UUID

from checkcheckserver.model._base_model import BaseTable, TimestampedModel
from checkcheckserver.model.checklist_color_scheme import ChecklistColorScheme

if TYPE_CHECKING:
    from checkcheckserver.model.checklist_item import CheckListItem

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


log = get_logger()
config = Config()


class CheckListItemStateUpdate(BaseTable, table=False):
    checked: bool = Field(default=False)


class CheckListItemStateApiCreate(CheckListItemStateUpdate, table=False):
    pass


class CheckListItemStateCreate(CheckListItemStateUpdate, table=False):
    checklist_item_id: uuid.UUID = Field(
        foreign_key="checklist_item.id", primary_key=True
    )


class CheckListItemState(CheckListItemStateCreate, table=True):
    __tablename__ = "checklist_item_state"
    checked: bool = Field()
    checklist_item: "CheckListItem" = Relationship(
        back_populates="state",
        sa_relationship_kwargs={"lazy": "joined", "single_parent": True},
    )


class CheckListItemStateWithoutChecklistID(CheckListItemStateCreate, table=False):
    checked: bool = Field()
    checklist_item_id: uuid.UUID = Field(
        foreign_key="checklist_item.id", primary_key=True, exclude=True
    )
