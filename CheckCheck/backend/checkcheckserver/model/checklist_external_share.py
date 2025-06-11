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
from sqlmodel import Field, UniqueConstraint

import uuid
from uuid import UUID

from checkcheckserver.model._base_model import BaseTable, TimestampedModel


from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


log = get_logger()
config = Config()


class CheckListExternalShareCreate(TimestampedModel, table=False):
    checklist_id: uuid.UUID = Field(foreign_key="checklist.id", primary_key=True)
    email: str = Field(foreign_key="user.id", primary_key=True)
    permission: Literal["write", "read"] = Field(
        default="read", description="Should the share allow to write into the checklist"
    )


class CheckListExternalShareC(CheckListExternalShareCreate, table=True):
    __tablename__ = "checklist_external_share"
