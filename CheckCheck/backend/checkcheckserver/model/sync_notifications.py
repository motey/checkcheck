from typing import AsyncGenerator, List, Optional, Literal, Sequence, Annotated
from pydantic import (
    validate_email,
    StringConstraints,
    field_validator,
    ValidationInfo,
    BaseModel,
)
import datetime
from fastapi import Depends
from typing import Optional
from sqlmodel import Field, UniqueConstraint, Relationship, String

import uuid
from uuid import UUID
import time
from checkcheckserver.model._base_model import BaseTable, TimestampedModel
from checkcheckserver.model.checklist_color_scheme import ChecklistColorScheme
from checkcheckserver.model.checklist_position import (
    CheckListPosition,
    CheckListPositionPublicWithoutChecklistID,
)

from checkcheckserver.model.user import User
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


log = get_logger()
config = Config()


class SyncNotification(BaseTable, table=True):
    timestamp: float = Field(
        description="Creation time of the notification",
        primary_key=True,
        default_factory=time.time,
    )
    cl_id: uuid.UUID = Field(
        description="Checklist that has to be updated by the client"
    )
    cli_id: Optional[uuid.UUID] = Field(
        description="ID of Checklist item that has to be updated by the client"
    )
    upd_prop: Literal["position", "text", "state", "color"] = Field(
        default=None, sa_type=String
    )


class SyncNotificationPackage(BaseModel):
    target_user_ids: List[uuid.UUID]
    notification: SyncNotification
