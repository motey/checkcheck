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


class CheckListItemPositionApiCreate(BaseTable, table=False):
    index: Optional[float] = Field(default=None, description="Position of the item")
    indentation: Optional[NonNegativeInt] = Field(
        default=0,
        description="To define 'sub'-items you can define an indentation level",
    )


class CheckListItemPositionApiUpdate(CheckListItemPositionApiCreate, table=False):
    indentation: Optional[NonNegativeInt] = Field(
        default=None,
        description="To define 'sub'-items you can define an indentation level",
    )


class CheckListItemPositionUpdate(CheckListItemPositionApiCreate, table=False):
    checklist_item_id: uuid.UUID = Field()
    index: Optional[float] = Field(default=None, description="Position of the item")
    indentation: Optional[NonNegativeInt] = Field(
        default=0,
        description="To define 'sub'-items you can define an indentation level",
    )


class CheckListItemPositionCreate(CheckListItemPositionUpdate, table=False):
    checklist_item_id: uuid.UUID = Field(
        foreign_key="checklist_item.id", primary_key=True
    )


class CheckListItemPosition(CheckListItemPositionCreate, table=True):
    __tablename__ = "checklist_item_pos"
    __table_args__ = (
        UniqueConstraint(
            "checklist_item_id",
            "index",
            name="Index must be unique per checklist",
        ),
    )
    index: float = Field(
        description="Position of the item. Lowest index means first in list."
    )
    indentation: NonNegativeInt = Field(
        description="To define 'sub'-items you can define an indentation level",
    )
    checklist_item_id: uuid.UUID = Field(
        foreign_key="checklist_item.id", primary_key=True
    )
    checklist_item: "CheckListItem" = Relationship(
        back_populates="position",
        sa_relationship_kwargs={
            "lazy": "joined",
            "single_parent": True,
        },
    )


class CheckListItemPositionPublicWithoutChecklistID(
    CheckListItemPositionCreate, table=False
):
    index: float = Field(description="Position of the item")
    indentation: NonNegativeInt = Field(
        description="To define 'sub'-items you can define an indentation level",
    )
    checklist_item_id: uuid.UUID = Field(
        foreign_key="checklist_item.id", primary_key=True, exclude=True
    )
