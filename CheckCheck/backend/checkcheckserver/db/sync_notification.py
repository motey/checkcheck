from typing import List, Optional
import uuid
import json
import datetime

from sqlmodel import select, delete, and_, or_, col
from sqlalchemy import text

from checkcheckserver.config import Config, DbBackend
from checkcheckserver.log import get_logger
from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.model.checklist import CheckList
from checkcheckserver.model.checklist_collaborator import (
    CheckListCollaborator,
    ShareStatus,
)
from checkcheckserver.model.checklist_public_share import CheckListPublicShare
from checkcheckserver.model.sync_notifications import SyncNotification, SyncNotificationPackage
from checkcheckserver.db.sync_seq import get_current_server_seq

log = get_logger()
config = Config()


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class SyncNotifiationCRUD(
    create_crud_base(
        table_model=SyncNotification,
        read_model=SyncNotification,
        create_model=SyncNotification,
        update_model=SyncNotification,
    )
):

    async def resolve_target_user_ids(self, cl_id: uuid.UUID) -> List[uuid.UUID]:
        """Public wrapper so callers can capture the target set *before* a
        delete mutates the rows it is resolved from (see the delete flows in
        routes_checklist / routes_checklist_share)."""
        return await self._resolve_target_user_ids(cl_id)

    async def _resolve_target_tokens(self, cl_id: uuid.UUID) -> List[str]:
        """Tokens of the checklist's currently-active public links (enabled +
        not expired). Connected anonymous SSE clients are addressed by token, so
        this is the anonymous analogue of _resolve_target_user_ids."""
        now = _utcnow()
        res = await self.session.exec(
            select(CheckListPublicShare.token).where(
                and_(
                    CheckListPublicShare.checklist_id == cl_id,
                    CheckListPublicShare.enabled == True,  # noqa: E712
                    or_(
                        col(CheckListPublicShare.expires_at).is_(None),
                        CheckListPublicShare.expires_at > now,
                    ),
                )
            )
        )
        return list(res.all())

    async def _resolve_target_user_ids(self, cl_id: uuid.UUID) -> List[uuid.UUID]:
        owner_res = await self.session.exec(
            select(CheckList.owner_id).where(CheckList.id == cl_id)
        )
        owner_id = owner_res.one_or_none()

        collab_res = await self.session.exec(
            select(CheckListCollaborator.user_id).where(
                and_(
                    CheckListCollaborator.checklist_id == cl_id,
                    # A pending/declined invitee is not a live viewer yet, so it is
                    # not fanned out ordinary edits (the invite notification itself
                    # is pinned to the invitee at the call site — see upsert_share).
                    CheckListCollaborator.status == ShareStatus.accepted.value,
                )
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

        if noti.target_user_ids is not None:
            target_ids = [uuid.UUID(uid) for uid in noti.target_user_ids]
        else:
            target_ids = await self._resolve_target_user_ids(noti.cl_id)

        if noti.target_tokens is not None:
            target_tokens = list(noti.target_tokens)
        else:
            target_tokens = await self._resolve_target_tokens(noti.cl_id)

        await self.session.exec(
            delete(SyncNotification).where(SyncNotification.id == noti.id)
        )
        await self.session.commit()
        return SyncNotificationPackage(
            target_user_ids=target_ids,
            target_tokens=target_tokens,
            notification=noti,
        )

    async def create(
        self,
        noti: SyncNotification,
        target_user_ids: Optional[List[uuid.UUID]] = None,
        target_tokens: Optional[List[str]] = None,
    ):
        """Emit a sync notification.

        ``target_user_ids`` explicitly pins who should receive it. Pass it for
        events that delete the rows target resolution relies on (deleting a
        checklist, revoking/leaving a share) — there the live DB state no longer
        identifies the right recipients. When omitted, targets are resolved
        dynamically from the checklist's owner + current collaborators.

        ``target_tokens`` is the anonymous analogue: public-share tokens of
        connected logged-out viewers. When omitted it is resolved dynamically
        from the checklist's currently-active public links, so ordinary edits
        reach anonymous viewers live without any extra plumbing at the call site.

        WI-5: alongside every *board-mutating* per-entity event, a lightweight
        ``changes_available`` poke is emitted to the **same** recipients, carrying
        the current global ``server_seq``. It is the single signal a local-first
        client subscribes to (it pulls ``GET /api/changes`` and can skip the pull
        when the poke's seq is <= its cursor). The legacy per-entity payload is
        left byte-for-byte unchanged (the poke is an *additional* message).
        ``notification`` (personal bell events, not board data returned by the
        delta feed) and the poke itself get no poke — avoids useless pulls and
        recursion.
        """
        await self._emit(noti, target_user_ids, target_tokens)

        if noti.upd_prop not in ("changes_available", "notification"):
            poke = SyncNotification(
                cl_id=noti.cl_id,
                upd_prop="changes_available",
                server_seq=await get_current_server_seq(self.session),
            )
            # Route the poke to the SAME recipients as the event that triggered it
            # (crucially reusing the explicit targets on delete/revoke, where the
            # DB no longer identifies who lost access).
            await self._emit(poke, target_user_ids, target_tokens)

    async def _emit(
        self,
        noti: SyncNotification,
        target_user_ids: Optional[List[uuid.UUID]] = None,
        target_tokens: Optional[List[str]] = None,
    ):
        """Deliver a single notification over the active backend's transport.

        Split out of ``create`` so the per-entity event and its ``changes_available``
        poke share identical target-resolution + transport handling.
        """
        if config.db_backend == DbBackend.POSTGRES:
            target_ids = (
                target_user_ids
                if target_user_ids is not None
                else await self._resolve_target_user_ids(noti.cl_id)
            )
            target_tok = (
                target_tokens
                if target_tokens is not None
                else await self._resolve_target_tokens(noti.cl_id)
            )
            payload = json.dumps({
                "timestamp": noti.timestamp,
                "cl_id": str(noti.cl_id),
                "cli_id": str(noti.cli_id) if noti.cli_id else None,
                "upd_prop": noti.upd_prop,
                "server_seq": noti.server_seq,
                "target_user_ids": [str(uid) for uid in target_ids],
                "target_tokens": list(target_tok),
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
            # Explicit targets are persisted on the row because the drain loop
            # resolves recipients lazily, long after the source rows are gone.
            if target_user_ids is not None:
                noti.target_user_ids = [str(uid) for uid in target_user_ids]
            if target_tokens is not None:
                noti.target_tokens = list(target_tokens)
            self.session.add(noti)
            await self.session.commit()
