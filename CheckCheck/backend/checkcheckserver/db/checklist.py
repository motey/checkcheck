from typing import AsyncGenerator, List, Optional, Literal, Sequence, Annotated, Tuple
from pydantic import validate_email, validator, StringConstraints
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
    and_,
    or_,
    desc,
)

import uuid
from uuid import UUID
from sqlmodel.sql import expression as sqlEpression
from sqlalchemy.orm import selectinload, with_loader_criteria
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model.checklist import (
    CheckList,
    CheckListUpdate,
    CheckListCreate,
)
from checkcheckserver.model.checklist_collaborator import (
    CheckListCollaborator,
    CheckListCollaboratorCreate,
)
from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.api.paginator import QueryParamsInterface
from checkcheckserver.model.checklist_position import CheckListPosition
from checkcheckserver.model.checklist import CheckListApi, CheckListApiWithSubObj
from checkcheckserver.model.label import Label
from checkcheckserver.model.checklist_label import CheckListLabel

log = get_logger()
config = Config()


class CheckListCRUD(
    create_crud_base(
        table_model=CheckList,
        read_model=CheckList,
        create_model=CheckListCreate,
        update_model=CheckListUpdate,
    )
):

    def _add_user_has_access_query(
        self,
        query: sqlEpression.Select,
        user_id: uuid.UUID,
        join_CheckListPosition: bool = True,
    ):
        query = query.join(
            CheckListCollaborator,
            isouter=True,
        ).where(
            or_(CheckList.owner_id == user_id, CheckListCollaborator.user_id == user_id)
        )
        if join_CheckListPosition:
            query = query.join(
                CheckListPosition,
                onclause=and_(
                    CheckListPosition.user_id == user_id,
                    CheckListPosition.checklist_id == CheckList.id,
                ),
            )
        return query

    async def count(
        self,
        user_id: uuid.UUID,
        archived: Optional[bool] = None,
    ) -> int:
        query = select(func.count()).select_from(CheckList)

        if archived is not None:
            query = query.where(CheckListPosition.archived == archived)
        query = self._add_user_has_access_query(query, user_id)

        results = await self.session.exec(statement=query)
        return results.first()

    async def list_access_ids(
        self,
        user_id: uuid.UUID,
    ) -> List[uuid.UUID]:
        """List all IDs of CheckLists the user has access to, due to ownership or collaboration permissions

        Args:
            user_id (uuid.UUID): _description_

        Returns:
            List[uuid.UUID]: _description_
        """
        query = select(CheckList.id)
        query = self._add_user_has_access_query(query, user_id)
        results = await self.session.exec(statement=query)
        return results.all()

    async def list(
        self,
        user_id: uuid.UUID,
        include_sub_obj: bool = False,
        archived: Optional[bool] = None,
        label_id: Optional[uuid.UUID] = None,
        pagination: QueryParamsInterface = None,
    ) -> List[CheckList | CheckListApiWithSubObj]:
        query = select(CheckList)
        query = self._add_user_has_access_query(
            query, user_id, join_CheckListPosition=True
        )

        if archived is not None:
            query = query.where(CheckListPosition.archived == archived)
        if label_id is not None:
            query = query.join(CheckListLabel).where(
                CheckListLabel.label_id == label_id
            )
        query = query.order_by(desc(CheckListPosition.index))
        if pagination:
            query = pagination.append_to_query(query)
        if include_sub_obj:
            query = query.options(selectinload(CheckList.position))
            query = query.options(selectinload(CheckList.color))
            query = query.options(
                selectinload(CheckList.labels),
                with_loader_criteria(CheckListLabel, CheckListLabel.user_id == user_id),
            )
            """
            query = query.join(
                CheckListLabel,
                onclause=and_(
                    CheckListLabel.checklist_id == CheckList.id,
                    CheckListLabel.user_id == user_id,
                ),
            ).join(Label)
            """
        # log.debug(f"list.checklist.query: {query}")
        results = await self.session.exec(statement=query)
        objs = results.all()
        return objs
