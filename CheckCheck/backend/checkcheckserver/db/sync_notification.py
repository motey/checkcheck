from typing import AsyncGenerator, List, Optional, Literal, Sequence, Annotated, Tuple
from pydantic import validate_email, validator, StringConstraints
from pydantic_core import PydanticCustomError
from fastapi import Depends
import contextlib
from typing import Optional
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import Field, select, delete, Column, JSON, SQLModel, func, col, and_, or_

import uuid
from uuid import UUID

from sqlalchemy.orm import selectinload
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger

from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.model.user import User
from checkcheckserver.model.checklist import CheckList
from checkcheckserver.model.checklist_collaborator import CheckListCollaborator

from checkcheckserver.model.sync_notifications import (
    SyncNotification,
    SyncNotificationPackage,
)

log = get_logger()
config = Config()


class SyncNotifiationCRUD(
    create_crud_base(
        table_model=SyncNotification,
        read_model=SyncNotification,
        create_model=SyncNotification,
        update_model=SyncNotification,
    )
):

    async def fetch_next_notificaton(self) -> SyncNotificationPackage | None:
        query = select(SyncNotification).order_by(SyncNotification.timestamp).limit(1)

        res = await self.session.exec(query)
        noti = res.one_or_none()

        checklist_item_id = noti.cl_id

        ## get checklist owner
        owner_query = select(CheckList.owner_id).where(
            CheckList.id == checklist_item_id
        )
        res = await self.session.exec(owner_query)
        owner_id = owner_query.one()

        ## get collaborators ids
        collab_user_ids_query = select(CheckListCollaborator.user_id).where(
            CheckListCollaborator.checklist_id == checklist_item_id
        )
        res = await self.session.exec(collab_user_ids_query)
        collab_user_ids = res.all()

        if noti is None:
            return None
        del_statement = delete(SyncNotification).where(
            SyncNotification.timestamp == noti.timestamp
        )
        await self.session.exec(del_statement)
        await self.session.commit()
        return SyncNotificationPackage(
            target_user_ids=collab_user_ids + [owner_id], notification=noti
        )

    async def fetch_next_event_for_user(
        self, user_id: uuid.UUID
    ) -> SyncNotificationPackage | None:
        query = select(SyncNotification).order_by(SyncNotification.timestamp).limit(1)

        res = await self.session.exec(query)
        noti = res.one_or_none()

        checklist_item_id = noti.cl_id

        ## get checklist owner
        owner_query = select(CheckList.owner_id).where(
            CheckList.id == checklist_item_id
        )
        res = await self.session.exec(owner_query)
        owner_id = owner_query.one()
        if user_id == owner_id:
            return noti

        ## get collaborators ids
        collab_user_ids_query = select(CheckListCollaborator.user_id).where(
            CheckListCollaborator.checklist_id == checklist_item_id
        )
        res = await self.session.exec(collab_user_ids_query)
        collab_user_ids = res.all()
        if user_id in collab_user_ids:
            return noti
        return None

        if noti is None:
            return None
        del_statement = delete(SyncNotification).where(
            SyncNotification.timestamp == noti.timestamp
        )
        await self.session.exec(del_statement)
        await self.session.commit()
        return SyncNotificationPackage(
            target_user_ids=collab_user_ids + [owner_id], notification=noti
        )

    async def create(self, noti: SyncNotification):
        self.session.add(noti)
        await self.session.commit()
