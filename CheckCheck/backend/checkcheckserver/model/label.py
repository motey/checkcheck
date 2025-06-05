from typing import (
    AsyncGenerator,
    List,
    Optional,
    Literal,
    Sequence,
    Annotated,
    Dict,
    TYPE_CHECKING,
)
import enum
from pydantic import (
    validate_email,
    field_validator,
    model_validator,
    StringConstraints,
    ValidationInfo,
)
from fastapi import Depends
from typing import Optional
from sqlmodel import Field, select, delete, Column, JSON, SQLModel, desc, Relationship
from datetime import datetime, timezone, date
import uuid
from uuid import UUID

from checkcheckserver.model.user import User
from checkcheckserver.model._base_model import (
    BaseTable,
    TimestampedModel,
)
from checkcheckserver.model.checklist_color_scheme import ChecklistColorScheme
from checkcheckserver.model.checklist_label import CheckListLabel

if TYPE_CHECKING:
    from checkcheckserver.model.checklist import CheckList
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger

log = get_logger()
config = Config()


class LabelCreate(BaseTable, table=False):
    color_id: Optional[str] = Field(
        foreign_key="checklist_color_scheme.id",
        default=None,
    )
    display_name: str = Field(description="Name of the label.", max_length="32")
    sort_order: Optional[int] = Field(
        description="Label order per user",
        default=None,
    )


class LabelUpdate(LabelCreate, table=False):
    color_id: Optional[str] = Field(
        foreign_key="checklist_color_scheme.id",
        default=None,
    )
    display_name: Optional[str] = Field(
        description="Name of the label.", max_length="32", default=None
    )
    sort_order: Optional[int] = Field(
        description="Label order per user",
        default=None,
    )


class LabelReadAPI(LabelUpdate, table=False):
    id: uuid.UUID
    owner_id: uuid.UUID
    color: Optional[ChecklistColorScheme]


class Label(LabelUpdate, table=True):
    __tablename__ = "label"
    id: uuid.UUID = Field(
        primary_key=True,
        index=True,
        nullable=False,
        unique=True,
        default_factory=uuid.uuid4,
    )
    owner_id: uuid.UUID = Field(foreign_key="user.id")

    color_id: Optional[str] = Field(
        foreign_key="checklist_color_scheme.id",
        default=None,
    )
    display_name: str = Field(description="Name of the label.", max_length="32")
    sort_order: Optional[int] = Field(
        description="Label order per user",
        default=None,
    )
    color: Optional[ChecklistColorScheme] = Relationship(
        sa_relationship_kwargs={"lazy": "joined"}
    )
