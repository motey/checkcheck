from typing import (
    AsyncGenerator,
    List,
    Optional,
    Literal,
    Sequence,
    Annotated,
    Tuple,
    Dict,
)
from pydantic import validate_email, StringConstraints, PositiveInt
from pydantic_core import PydanticCustomError
from fastapi import Depends
import contextlib
from typing import Optional
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import Field, select, delete, Column, JSON, SQLModel, func, col
from sqlalchemy.orm import selectinload

import uuid
from uuid import UUID


from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model.checklist_color_scheme import (
    ChecklistColorScheme,
)

from checkcheckserver.model.label import Label, LabelUpdate

from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.api.paginator import QueryParamsInterface


log = get_logger()
config = Config()


class LabelCRUD(
    create_crud_base(
        table_model=Label,
        read_model=Label,
        create_model=Label,
        update_model=LabelUpdate,
    )
):

    async def list(
        self,
        user_id: uuid.UUID,
    ) -> List[Label]:
        query = select(Label).where(Label.owner_id == user_id)

        query = query.order_by(Label.sort_order)

        query = query.options(selectinload(Label.color))
        # log.debug(f"list.checklist.query: {query}")
        results = await self.session.exec(statement=query)
        objs = results.all()
        return objs
