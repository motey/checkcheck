from typing import AsyncGenerator, List, Optional, Literal, Sequence, Annotated
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
from sqlmodel import Field, UniqueConstraint

import uuid
from uuid import UUID

from checkcheckserver.model._base_model import BaseTable, TimestampedModel
from checkcheckserver.model.checklist_color_scheme import ChecklistColorScheme

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


log = get_logger()
config = Config()


class CheckListCollaboratorCreate(TimestampedModel, table=False):
    checklist_id: uuid.UUID = Field(foreign_key="checklist.id", primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", primary_key=True)


class CheckListCollaborator(CheckListCollaboratorCreate, table=True):
    __tablename__ = "checklist_collaborator"
