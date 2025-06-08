from typing import (
    AsyncGenerator,
    List,
    Optional,
    Literal,
    Sequence,
    Annotated,
    Tuple,
    Dict,
    Any,
    Type,
)
from pydantic import StringConstraints, PositiveInt
from pydantic_core import PydanticCustomError
from fastapi import Depends
import contextlib
from typing import Optional
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import (
    Field,
    select,
    delete,
    Column,
    JSON,
    SQLModel,
    func,
    col,
    desc,
    case,
)
from collections import defaultdict
from sqlalchemy import func
from sqlalchemy.orm import (
    contains_eager,
    joinedload,
    selectinload,
    with_loader_criteria,
)

import uuid


from sqlalchemy.orm import aliased
from sqlalchemy.sql import over
from collections import defaultdict


from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model.checklist_item import (
    CheckListItem,
    CheckListItemCreate,
    CheckListItemUpdate,
)
from checkcheckserver.model.checklist_item_state import CheckListItemState
from checkcheckserver.model.checklist_item_position import CheckListItemPosition

from checkcheckserver.model.checklist import CheckList
from checkcheckserver.model.checklist_position import CheckListPosition

from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.api.paginator import QueryParamsInterface


log = get_logger()
config = Config()


class CheckListItemCRUD(
    create_crud_base(
        table_model=CheckListItem,
        read_model=CheckListItem,
        create_model=CheckListItemCreate,
        update_model=CheckListItemUpdate,
    )
):

    async def count(
        self,
        checklist_id: Optional[uuid.UUID] = None,
        checked: Optional[bool] = None,
    ) -> int:
        query = select(func.count()).select_from(CheckListItem)
        if checklist_id is not None:
            query = query.where(CheckListItem.checklist_id == checklist_id)
        if checked is not None:
            query = query.join(CheckListItemState).where(
                CheckListItemState.checked == checked
            )

        results = await self.session.exec(statement=query)
        return results.first()

    async def list(
        self,
        checklist_id: uuid.UUID,
        checked: Optional[bool] = None,
        pagination: QueryParamsInterface = None,
    ) -> List[CheckListItem]:
        query = (
            select(CheckListItem)
            .options(
                joinedload(CheckListItem.position), joinedload(CheckListItem.state)
            )
            .join(CheckListItemPosition)
            .where(CheckListItem.checklist_id == checklist_id)
            .order_by(CheckListItemPosition.index)
        )
        if checked is not None:
            if checked is not None:
                query.join(CheckListItemState).where(
                    CheckListItemState.checked == checked
                )
        if pagination:
            query = pagination.append_to_query(query)
        results = await self.session.exec(statement=query)
        return results.all()

    async def get(
        self, id_: uuid.UUID, raise_exception_if_none: Exception = None
    ) -> CheckListItem:
        query = (
            select(CheckListItem)
            .options(
                joinedload(CheckListItem.state),
                joinedload(CheckListItem.position),
            )
            .where(CheckListItem.id == id_)
        )
        results = await self.session.exec(statement=query)
        obj = results.one_or_none()
        if raise_exception_if_none and obj is None:
            raise raise_exception_if_none
        return obj

    async def list_multiple_checklist_items_old(
        self,
        checklist_ids: List[uuid.UUID],
        limit_per_checklist: Optional[PositiveInt] = 12,
        checked: Optional[bool] = None,
    ) -> Dict[uuid.UUID, List[CheckListItem]]:
        # Step 1: Fetch all relevant CheckLists first
        checklist_query = select(CheckList).where(CheckList.id.in_(checklist_ids))
        checklist_result = await self.session.exec(checklist_query)
        checklist_map = {c.id: c for c in checklist_result.unique().all()}

        # Step 2: One query to get all items matching the filter
        query = (
            select(CheckListItem)
            .join(CheckListItemState)
            .join(CheckListItemPosition)
            .where(col(CheckListItem.checklist_id).in_(checklist_ids))
            .options(
                contains_eager(CheckListItem.state),
                contains_eager(CheckListItem.position),
            )
        )

        if checked is not None:
            query = query.where(CheckListItemState.checked == checked)

        # Fetch all items
        query_result = await self.session.exec(query)
        all_items = query_result.all()

        # Step 3: Group and sort items in Python
        grouped_items: Dict[uuid.UUID, List[CheckListItem]] = defaultdict(list)
        for item in all_items:
            grouped_items[item.checklist_id].append(item)

        # Step 4: Sort and limit
        result: Dict[uuid.UUID, List[CheckListItem]] = {}
        for checklist_id, items in grouped_items.items():
            checklist = checklist_map[checklist_id]
            if checklist.checked_items_seperated:
                # First sort by checked state, then by index
                items.sort(key=lambda item: (item.state.checked, item.position.index))
            else:
                items.sort(key=lambda item: item.position.index)
            result[checklist_id] = items[:limit_per_checklist]

        return result

    async def list_multiple_checklist_items(
        self,
        checklist_ids: List[uuid.UUID],
        limit_per_checklist: Optional[PositiveInt] = 12,
        checked: Optional[bool] = None,
    ) -> Dict[uuid.UUID, List[CheckListItem]]:
        # Aliases
        cli = aliased(CheckListItem)
        state = aliased(CheckListItemState)
        pos = aliased(CheckListItemPosition)
        cl = aliased(CheckList)

        # Build base query with joins
        ordering_case = case(
            (cl.checked_items_seperated == True, state.checked), else_=None
        )
        row_number_expr: Column = over(
            func.row_number(),
            partition_by=cli.checklist_id,
            order_by=(ordering_case, pos.index),
        ).label("row_number")

        base_query = (
            select(
                cli,
                state,
                pos,
                cli.checklist_id,
                row_number_expr,
            )
            .join(state, cli.id == state.checklist_item_id)
            .join(pos, cli.id == pos.checklist_item_id)
            .join(cl, cli.checklist_id == cl.id)
            .where(col(cli.checklist_id).in_(checklist_ids))
        )

        if checked is not None:
            base_query = base_query.where(state.checked == checked)
        subquery = base_query.subquery()
        query = select(subquery).where(subquery.c.row_number <= limit_per_checklist)

        result = await self.session.execute(query)
        rows = result.mappings()
        # log.debug(f"rows {rows}")

        # Group by checklist_id
        grouped: Dict[uuid.UUID, List[CheckListItem]] = defaultdict(list)
        for row in rows:
            # TODO: because we pack our query in a subquery, we lose the SQLModel ORM mapping.
            # this hotfix works for now but we need to find a better solution, later

            # log.debug(("row", row))

            cli_res = CheckListItem.model_validate(row)
            cli_res.state = CheckListItemState.model_validate(row)
            cli_res.position = CheckListItemPosition.model_validate(row)
            # log.debug(("CLI_RES", cli_res))
            if cli_res.checklist_id not in grouped:
                grouped[cli_res.checklist_id] = []
            grouped[cli_res.checklist_id].append(cli_res)

        return grouped
