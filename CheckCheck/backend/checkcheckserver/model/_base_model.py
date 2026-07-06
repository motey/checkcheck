from typing import Optional
import datetime
from pydantic import Field, field_validator, ValidationInfo
from sqlalchemy import text, event as _sa_event
from sqlalchemy.orm import Mapper as _SAMapper
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


def naive_utc_now() -> datetime.datetime:
    """The one source of naive-UTC 'now' for row timestamps.

    Naive (tzinfo stripped) to match the project's datetime convention across
    both DB backends. Used as the ``default_factory`` for ``created_at`` /
    ``updated_at`` and by the ``before_update`` stamp so the server — never the
    client — sets the sync version signal.
    """
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class TimestampedModel(SQLModel):
    created_at: datetime.datetime = Field(
        default_factory=naive_utc_now,
        nullable=False,
    )

    # `updated_at` is the per-row version signal the 2.0 sync engine (WI-4 delta
    # feed) reads. It MUST be server-set — client clocks are never trusted for
    # ordering (LWW is server-arrival order).
    #
    # It is stamped by the `before_update` mapper event below (fires on every ORM
    # UPDATE flush, for every subclass, so no custom CRUD method can bypass it)
    # plus an explicit stamp in `CRUDBase.update` for the no-other-field-changed
    # case. We deliberately do NOT use a SQLAlchemy column `onupdate` here: on
    # multiply-inheriting `table=True` models SQLModel routes `sa_column_kwargs`
    # through `json_schema_extra`, and a callable there is not JSON-serializable,
    # which breaks OpenAPI generation. `default_factory` seeds it to ~`created_at`
    # on insert so there is no null to backfill.
    updated_at: datetime.datetime = Field(
        default_factory=naive_utc_now,
        nullable=False,
    )


# Server-stamp `updated_at` on every UPDATE flush. Registered on the generic
# Mapper (TimestampedModel itself is abstract / non-mapped) and gated by an
# isinstance check so it applies to exactly the timestamped tables. Fires only
# when the row is already dirty enough to emit an UPDATE; the no-op-update case
# is covered by the explicit stamp in `CRUDBase.update`.
@_sa_event.listens_for(_SAMapper, "before_update")
def _stamp_updated_at(mapper, connection, target):  # noqa: ANN001
    if isinstance(target, TimestampedModel):
        target.updated_at = naive_utc_now()
