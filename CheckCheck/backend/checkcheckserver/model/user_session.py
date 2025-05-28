# Basics
from typing import AsyncGenerator, List, Optional, Literal, Sequence

# Libs
import enum
import uuid
from pydantic import SecretStr
from sqlmodel import Field, Column, Enum, UniqueConstraint
from passlib.context import CryptContext
import secrets
import datetime

# Internal
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model._base_model import TimestampedModel, BaseTable
from checkcheckserver.model.user import User

import random
import string

log = get_logger()
config = Config()


class UserSessionCreate(BaseTable, table=False):
    user_id: uuid.UUID = Field(foreign_key="user.id")
    user_auth_id: uuid.UUID = Field(foreign_key="user_auth.id")
    session_name: Optional[str] = Field(
        default_factory=lambda: "".join(random.choices(string.ascii_lowercase, k=8))
    )
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    expires_at_epoch_time: Optional[int] = Field(
        default=None,
        description="The set time when the session expires or the OIDC token needs a refresh.",
    )

    def is_expired(self, leeway_sec: int = 30):
        if self.expires_at_epoch_time is None:
            return False
        return (
            self.expires_at_epoch_time
            < datetime.datetime.now(tz=datetime.UTC).timestamp() + leeway_sec
        )


class UserSession(UserSessionCreate, TimestampedModel, table=True):
    __tablename__ = "user_session"
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
        unique=True,
        # sa_column_kwargs={"server_default": text("gen_random_uuid()")},
    )
