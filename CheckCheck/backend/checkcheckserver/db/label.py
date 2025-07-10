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
from sqlmodel import Field, select, delete, Column, JSON, SQLModel, func, col, desc
from sqlalchemy.orm import selectinload

import uuid
from uuid import UUID


from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model.checklist_color_scheme import (
    ChecklistColorScheme,
)

from checkcheckserver.model.label import Label, LabelUpdate, LabelCreate

from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.api.paginator import QueryParamsInterface


log = get_logger()
config = Config()


class LabelCRUD(
    create_crud_base(
        table_model=Label,
        read_model=Label,
        create_model=LabelCreate,
        update_model=LabelUpdate,
    )
):

    async def list(
        self,
        user_id: uuid.UUID,
    ) -> List[Label]:
        query = select(Label).where(Label.owner_id == user_id)

        query = query.order_by(desc(Label.sort_order))

        query = query.options(selectinload(Label.color))
        # log.debug(f"list.checklist.query: {query}")
        results = await self.session.exec(statement=query)
        objs = results.all()
        return objs

    async def get_max_sort_order(
        self,
        user_id: uuid.UUID,
    ) -> int:
        query = (
            select(Label.sort_order)
            .where(Label.owner_id == user_id)
            .order_by(desc(Label.sort_order))
        ).limit(1)

        # log.debug(f"list.checklist.query: {query}")
        results = await self.session.exec(statement=query)
        return results.first()

    async def sort(
        self,
        user_id: uuid.UUID,
        label_order: List[uuid.UUID],
    ) -> List[Label]:
        query = select(Label).where(Label.owner_id == user_id).order_by(desc(Label.id))
        query = query.options(selectinload(Label.color))
        all_labels_results = await self.session.exec(statement=query)
        all_labels = all_labels_results.all()
        sorted_labels = []
        i = 10
        for label_id in label_order:
            next_label = next((l for l in all_labels if l.id == label_id), None)
            if next_label is not None:
                next_label.sort_order = i
                i = i + 10
                sorted_labels.append(next_label)

        # sort user labels are left (not included in label_order)
        for label in all_labels:
            if label.id not in label_order:
                label.sort_order == i
                i = i + 10
                sorted_labels.append(label)
        self.session.add(sorted_labels)
        await self.session.commit()

        results = await self.session.exec(statement=query)
        return sorted_labels
