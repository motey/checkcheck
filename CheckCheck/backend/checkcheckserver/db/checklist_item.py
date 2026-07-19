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
    and_,
    or_,
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
from checkcheckserver.model._base_model import naive_utc_now
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
        query = query.where(col(CheckListItem.deleted_at).is_(None))
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
            .where(col(CheckListItem.deleted_at).is_(None))
            .order_by(CheckListItemPosition.index)
        )
        if checked is not None:
            query = query.join(
                CheckListItemState,
                CheckListItemState.checklist_item_id == CheckListItem.id,
            ).where(CheckListItemState.checked == checked)
        if pagination:
            query = pagination.append_to_query(query)
        results = await self.session.exec(statement=query)
        return results.all()

    async def get(
        self,
        id_: uuid.UUID,
        raise_exception_if_none: Exception = None,
        include_deleted: bool = False,
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
        # Mask tombstoned items (WI-2) unless a tombstone-aware caller asks for
        # them (the 410 guards in the item routes).
        if obj is not None and not include_deleted and obj.deleted_at is not None:
            obj = None
        if raise_exception_if_none and obj is None:
            raise raise_exception_if_none
        return obj

    async def delete_checked_items(self, checklist_id: uuid.UUID) -> int:
        """Bulk "delete ticked": soft-delete (tombstone) every checked, live item
        of this checklist in a single transaction.

        MUST mutate ORM objects in a loop (not a Core ``DELETE`` / bulk
        ``UPDATE``): ``server_seq`` is stamped by the ``before_update`` mapper
        event, which fires only for dirtied ORM instances at flush — a bulk
        statement would tombstone the rows without bumping ``server_seq``, so the
        removals would never reach offline clients through the delta feed (see the
        bulk-op plan §1.2).

        Only the parent ``CheckListItem`` row is tombstoned; its state/position
        children are left in place and masked by the tombstone — hard-deleting a
        child in the same session would trip the cascade crash (see the
        tombstone-cascade gotcha). Idempotent: already-tombstoned items are
        excluded by the ``deleted_at IS NULL`` filter, so a replay only tombstones
        whatever is checked *now*.

        Returns the number of items tombstoned.
        """
        query = (
            select(CheckListItem)
            .join(
                CheckListItemState,
                CheckListItemState.checklist_item_id == CheckListItem.id,
            )
            .where(CheckListItem.checklist_id == checklist_id)
            .where(col(CheckListItem.deleted_at).is_(None))
            .where(CheckListItemState.checked == True)  # noqa: E712
        )
        result = await self.session.exec(query)
        items = result.unique().all()
        now = naive_utc_now()
        for item in items:
            item.deleted_at = now
            self.session.add(item)
        if items:
            await self.session.commit()
        return len(items)

    async def list_changed_items(
        self,
        since: int,
        changed_checklist_ids: List[uuid.UUID],
        full_checklist_ids: List[uuid.UUID],
    ) -> List[CheckListItem]:
        """Live items to ship in a delta pull (WI-4), with state + position eager
        loaded.

        Two scopes, OR-ed:
        * ``changed_checklist_ids`` — cards the caller already has; return only the
          items whose own row, ``state`` (checked) or ``position`` changed after
          ``since``.
        * ``full_checklist_ids`` — cards the caller just gained access to; their
          whole tree predates the access grant (lower ``server_seq``), so return
          *all* live items regardless of ``since``.
        The two sets are disjoint at the call site; overlap would merely OR to the
        same rows."""
        conditions = []
        if full_checklist_ids:
            conditions.append(
                col(CheckListItem.checklist_id).in_(full_checklist_ids)
            )
        if changed_checklist_ids:
            conditions.append(
                and_(
                    col(CheckListItem.checklist_id).in_(changed_checklist_ids),
                    or_(
                        col(CheckListItem.server_seq) > since,
                        col(CheckListItemState.server_seq) > since,
                        col(CheckListItemPosition.server_seq) > since,
                    ),
                )
            )
        if not conditions:
            return []
        query = (
            select(CheckListItem)
            .join(
                CheckListItemState,
                CheckListItem.id == CheckListItemState.checklist_item_id,
            )
            .join(
                CheckListItemPosition,
                CheckListItem.id == CheckListItemPosition.checklist_item_id,
            )
            .where(col(CheckListItem.deleted_at).is_(None))
            .where(or_(*conditions))
            .options(
                contains_eager(CheckListItem.state),
                contains_eager(CheckListItem.position),
            )
        )
        results = await self.session.exec(statement=query)
        return list(results.unique().all())

    async def list_tombstoned_item_ids(
        self,
        checklist_ids: List[uuid.UUID],
        since: int,
    ) -> List[uuid.UUID]:
        """Ids of items tombstoned after ``since`` within the given (currently
        accessible) cards. Items of a *tombstoned card* are intentionally excluded
        — the client drops the whole card via ``checklist_tombstones`` — so callers
        pass only live-accessible checklist ids."""
        if not checklist_ids:
            return []
        query = select(CheckListItem.id).where(
            col(CheckListItem.checklist_id).in_(checklist_ids),
            col(CheckListItem.deleted_at).is_not(None),
            col(CheckListItem.server_seq) > since,
        )
        results = await self.session.exec(statement=query)
        return list(results.all())

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
            .where(col(CheckListItem.deleted_at).is_(None))
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
            .where(col(cli.deleted_at).is_(None))
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
