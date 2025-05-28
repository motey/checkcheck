from typing import AsyncGenerator, List, Optional, Literal, Sequence, Annotated
from pydantic import validate_email, validator, StringConstraints
from fastapi import Depends
from typing import Optional
from sqlmodel import Field

import uuid
from uuid import UUID

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model._base_model import BaseTable, TimestampedModel

log = get_logger()
config = Config()


class HealthCheck(TimestampedModel):
    healthy: bool


class HealthCheckReport(TimestampedModel):
    name: str
    version: str
    db_working: bool
