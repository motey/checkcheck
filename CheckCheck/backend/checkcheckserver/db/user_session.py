# Basics
from typing import AsyncGenerator, List, Optional, Literal, Sequence

# Libs
import uuid
from fastapi import Depends, HTTPException, status
from sqlmodel import Field, select, delete, Enum, Column

import datetime

# Internal
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model._base_model import TimestampedModel
from checkcheckserver.db._session import AsyncSession, get_async_session
from checkcheckserver.model.user import User
from checkcheckserver.model.user_session import UserSession, UserSessionCreate
from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.api.paginator import QueryParamsInterface

log = get_logger()
config = Config()


class UserSessionCRUD(
    create_crud_base(
        table_model=UserSession,
        read_model=UserSession,
        create_model=UserSessionCreate,
        update_model=UserSession,
    )
):

    async def get_by_user_auth_id(
        self, user_auth_id: uuid.UUID, raise_exception_if_none: Exception = None
    ) -> UserSession | None:
        query = select(UserSession).where(UserSession.user_auth_id == user_auth_id)
        results = await self.session.exec(statement=query)
        user_session: UserSession | None = results.one_or_none()

        if user_session is None and raise_exception_if_none:
            raise raise_exception_if_none
        return user_session
