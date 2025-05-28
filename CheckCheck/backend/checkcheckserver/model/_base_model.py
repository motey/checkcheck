from typing import Optional
import datetime
from pydantic import Field, field_validator, ValidationInfo
from sqlalchemy import text
import uuid


from sqlmodel import SQLModel


from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


log = get_logger()
config = Config()
import uuid


class BaseTable(SQLModel):
    # Absolute base class. All model classes will inherhit from this class.
    """
    # cast all ids to UUIDs
    @field_validator("id", check_fields=False)
    @classmethod
    def id_to_uuid(cls, v: str | uuid.UUID, info: ValidationInfo) -> uuid.UUID:
        if isinstance(v, str):
            v = uuid.UUID(v)
        return v
    """


""" 
# we can not outsource the primary key to a parent base model. sqlalchemy does not like that and throws an error in model init. e.g. 
# "sqlalchemy.exc.ArgumentError: Mapper Mapper[User(user)] could not assemble any primary key columns for mapped table 'user'"
# saaad. very saaaad.
class UUIDModel(SQLModel):
    pk: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
        unique=True,
        ## sa_column_kwargs={"server_default": text("gen_random_uuid()")},
    )
"""


class TimestampedModel(SQLModel):
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(tz=datetime.timezone.utc),
        nullable=False,
    )

    ## this is broken because fastapi/pydantic does not like the "sqlalchemy.text()" part.
    # todo: (with reasonable effort) find a solution to implement a way to implement an updated_at column/function.
    """
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={
            "onupdate": text("current_timestamp(0)"),
        },
    )
    """
