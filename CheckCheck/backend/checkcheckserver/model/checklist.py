from typing import AsyncGenerator, List, Optional, Literal, Sequence, Annotated
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
from checkcheckserver.model.label import Label
from checkcheckserver.model.checklist_label import CheckListLabel
from checkcheckserver.model.user import User
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


log = get_logger()
config = Config()


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


class CheckListApiCreate(CheckListBase):
    position: CheckListPositionApiCreate | None = None


class CheckListApiWithSubObj(CheckListApi):
    color: Optional[ChecklistColorScheme]
    position: CheckListPositionPublicWithoutChecklistID
    labels: list[Label]
