from typing import List, Optional
import uuid
import json

from sqlmodel import select, delete
from sqlalchemy import text

from checkcheckserver.config import Config, DbBackend
from checkcheckserver.log import get_logger
from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.model.checklist import CheckList
from checkcheckserver.model.checklist_collaborator import CheckListCollaborator
from checkcheckserver.model.sync_notifications import SyncNotification, SyncNotificationPackage

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

    async def _resolve_target_user_ids(self, cl_id: uuid.UUID) -> List[uuid.UUID]:
        owner_res = await self.session.exec(
            select(CheckList.owner_id).where(CheckList.id == cl_id)
        )
        owner_id = owner_res.one_or_none()

        collab_res = await self.session.exec(
            select(CheckListCollaborator.user_id).where(
                CheckListCollaborator.checklist_id == cl_id
            )
        )
        target_ids = list(collab_res.all())
        if owner_id is not None:
            target_ids.append(owner_id)
        return target_ids

    async def fetch_next_notificaton(self) -> SyncNotificationPackage | None:
        """SQLite only. Fetch and delete the oldest pending notification."""
        res = await self.session.exec(
            select(SyncNotification).order_by(SyncNotification.id).limit(1)
        )
        noti = res.one_or_none()
        if noti is None:
            return None

        target_ids = await self._resolve_target_user_ids(noti.cl_id)

        await self.session.exec(
            delete(SyncNotification).where(SyncNotification.id == noti.id)
        )
        await self.session.commit()
        return SyncNotificationPackage(target_user_ids=target_ids, notification=noti)

    async def create(self, noti: SyncNotification):
        if config.db_backend == DbBackend.POSTGRES:
            target_ids = await self._resolve_target_user_ids(noti.cl_id)
            payload = json.dumps({
                "timestamp": noti.timestamp,
                "cl_id": str(noti.cl_id),
                "cli_id": str(noti.cli_id) if noti.cli_id else None,
                "upd_prop": noti.upd_prop,
                "target_user_ids": [str(uid) for uid in target_ids],
            })
            await self.session.execute(
                text("SELECT pg_notify('checkcheck_sync', :payload)"),
                {"payload": payload},
            )
            await self.session.commit()
        else:
            # SQLite: plain INSERT. The drain loop delivers notifications in
            # order; the frontend debounce collapses any high-frequency bursts.
            # A (cl_id, cli_id, upd_prop) upsert would silently drop updates
            # for different items sharing the same upd_prop (e.g. two items
            # moved in rapid succession), so we do a plain INSERT instead.
            self.session.add(noti)
            await self.session.commit()
