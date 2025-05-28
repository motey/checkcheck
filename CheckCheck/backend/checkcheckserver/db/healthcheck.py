from typing import AsyncGenerator, List, Optional, Literal, Sequence, Annotated, Dict
from pydantic import validate_email, StringConstraints, field_validator, model_validator
from pydantic_core import PydanticCustomError
from fastapi import Depends
import contextlib
from typing import Optional
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import Field, select, delete, Column, JSON, SQLModel

import uuid
from uuid import UUID
from getversion import get_module_version
import checkcheckserver
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model.healthcheck import HealthCheck, HealthCheckReport
from checkcheckserver.api.paginator import QueryParamsInterface

from checkcheckserver.db._session import get_async_session_context

from checkcheckserver.db._base_crud import DatabaseInteractionBase

log = get_logger()
config = Config()


class HealthcheckRead(DatabaseInteractionBase):

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(
        self,
    ) -> HealthCheck:
        # basic db check
        query = select(1)
        results = await self.session.exec(statement=query)
        if results.first() == 1:
            return HealthCheck(healthy=True)
        return HealthCheck(healthy=False)

    async def get_report(
        self,
    ) -> HealthCheckReport:
        healthcheck = HealthCheckReport(
            name=config.APP_NAME,
            version=get_module_version(checkcheckserver)[0],
            db_working=False,
        )
        # basic db check
        query = select(1)
        results = await self.session.exec(statement=query)
        if results.first() == 1:
            healthcheck.db_working = True
        return healthcheck
