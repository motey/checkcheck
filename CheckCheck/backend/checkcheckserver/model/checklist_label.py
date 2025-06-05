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
)
import datetime
from fastapi import Depends
from typing import Optional
from sqlmodel import Field, UniqueConstraint, Relationship

import uuid
from uuid import UUID

from checkcheckserver.model._base_model import BaseTable, TimestampedModel

if TYPE_CHECKING:
    from checkcheckserver.model.label import Label
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


log = get_logger()
config = Config()


class CheckListLabelCreate(BaseTable, table=False):
    checklist_id: uuid.UUID = Field(foreign_key="checklist.id", primary_key=True)
    label_id: uuid.UUID = Field(foreign_key="label.id", primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", primary_key=True)


class CheckListLabel(CheckListLabelCreate, table=True):
    __tablename__ = "checklist_label"
    label: "Label" = Relationship(sa_relationship_kwargs={"lazy": "joined"})
