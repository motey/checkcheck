"""CRUD for in-app notifications (Phase 9 of card sharing).

``emit_notification`` is the **single internal seam** the plan calls out: every
notification row is created here, so a later "notification transports"
sub-project can fan the same event out to email/SMTP without touching any caller.
It both persists the row and pushes a lightweight ``upd_prop="notification"`` over
the existing SSE so a connected client refreshes its feed/badge live.
"""

import datetime
import uuid
from typing import List, Optional

from sqlmodel import select, update, and_, col, func

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.db.sync_notification import SyncNotifiationCRUD
from checkcheckserver.model.sync_notifications import SyncNotification
from checkcheckserver.model.notification import (
    Notification,
    NotificationCreate,
    NotificationType,
)


log = get_logger()
config = Config()


def _utcnow() -> datetime.datetime:
    # Naive UTC to match the timestamps stored on the model (see TimestampedModel).
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class NotificationCRUD(
    create_crud_base(
        table_model=Notification,
        read_model=Notification,
        create_model=NotificationCreate,
        update_model=Notification,
    )
):

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        unread_only: bool = False,
        limit: int = 100,
    ) -> List[Notification]:
        """The user's feed, newest first, bounded. ``unread_only`` keeps only the
        rows that have not been marked read."""
        query = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            query = query.where(col(Notification.read_at).is_(None))
        query = query.order_by(col(Notification.created_at).desc()).limit(limit)
        results = await self.session.exec(query)
        return list(results.all())

    async def unread_count(self, user_id: uuid.UUID) -> int:
        query = (
            select(func.count())
            .select_from(Notification)
            .where(
                and_(
                    Notification.user_id == user_id,
                    col(Notification.read_at).is_(None),
                )
            )
        )
        results = await self.session.exec(query)
        return results.one()

    async def mark_read(
        self, notification_id: uuid.UUID, user_id: uuid.UUID
    ) -> Optional[Notification]:
        """Mark one notification read, but only if it belongs to ``user_id`` (so a
        user cannot flip someone else's notifications). Returns None if there is no
        such row owned by the user. Idempotent — re-marking keeps the first time."""
        noti = await self._get(notification_id)
        if noti is None or noti.user_id != user_id:
            return None
        if noti.read_at is None:
            noti.read_at = _utcnow()
            self.session.add(noti)
            await self.session.commit()
            await self.session.refresh(noti)
        return noti

    async def mark_all_read(self, user_id: uuid.UUID) -> None:
        await self.session.exec(
            update(Notification)
            .where(
                and_(
                    Notification.user_id == user_id,
                    col(Notification.read_at).is_(None),
                )
            )
            .values(read_at=_utcnow())
        )
        await self.session.commit()


async def emit_notification(
    notification_crud: NotificationCRUD,
    sync_crud: SyncNotifiationCRUD,
    *,
    user_id: uuid.UUID,
    type: NotificationType,
    cl_id: uuid.UUID,
    payload: Optional[dict] = None,
) -> Notification:
    """Create a notification row and nudge the recipient's live SSE feed.

    This is the one place a ``Notification`` is created — a later email/SMTP
    transport sub-project hooks in here. ``cl_id`` is required because every
    notification refers to a card and the SSE envelope (``SyncNotification.cl_id``)
    is non-nullable; the push is pinned to ``user_id`` so only the recipient's
    connected clients refresh.
    """
    noti = await notification_crud.create(
        NotificationCreate(
            user_id=user_id,
            type=type,
            cl_id=cl_id,
            payload=payload,
        )
    )
    await sync_crud.create(
        SyncNotification(cl_id=cl_id, upd_prop="notification"),
        target_user_ids=[user_id],
    )
    return noti
