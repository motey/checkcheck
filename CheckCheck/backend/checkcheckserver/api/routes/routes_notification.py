"""In-app notification feed API (Phase 9 of card sharing).

Authenticated self-service endpoints under ``/user/me/notifications``. The feed is
populated by ``emit_notification`` at the share events (see
``checkcheckserver.db.notification``); a connected client is nudged to refresh via
the existing SSE (``upd_prop="notification"``).
"""

import datetime
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Security, HTTPException, status, Query
from pydantic import BaseModel, Field

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger

from checkcheckserver.db.user import User
from checkcheckserver.api.auth.security import get_current_user
from checkcheckserver.db.notification import NotificationCRUD
from checkcheckserver.model.notification import Notification, NotificationType


config = Config()
log = get_logger()

fast_api_notification_router: APIRouter = APIRouter()


class NotificationRead(BaseModel):
    id: uuid.UUID
    type: NotificationType
    cl_id: Optional[uuid.UUID] = None
    payload: Optional[dict] = None
    read_at: Optional[datetime.datetime] = None
    created_at: datetime.datetime


class UnreadCountResult(BaseModel):
    unread_count: int = Field(description="Number of unread notifications.")


def _to_read(noti: Notification) -> NotificationRead:
    return NotificationRead(
        id=noti.id,
        type=noti.type,
        cl_id=noti.cl_id,
        payload=noti.payload,
        read_at=noti.read_at,
        created_at=noti.created_at,
    )


@fast_api_notification_router.get(
    "/user/me/notifications",
    response_model=List[NotificationRead],
    description="The current user's notification feed, newest first.",
)
async def list_my_notifications(
    unread_only: bool = Query(
        default=False, description="Return only notifications that are still unread."
    ),
    limit: int = Query(default=100, ge=1, le=200),
    current_user: User = Security(get_current_user),
    notification_crud: NotificationCRUD = Depends(NotificationCRUD.get_crud),
) -> List[NotificationRead]:
    notifications = await notification_crud.list_for_user(
        user_id=current_user.id, unread_only=unread_only, limit=limit
    )
    return [_to_read(n) for n in notifications]


@fast_api_notification_router.get(
    "/user/me/notifications/unread-count",
    response_model=UnreadCountResult,
    description="How many unread notifications the current user has (for a badge).",
)
async def my_unread_notification_count(
    current_user: User = Security(get_current_user),
    notification_crud: NotificationCRUD = Depends(NotificationCRUD.get_crud),
) -> UnreadCountResult:
    return UnreadCountResult(
        unread_count=await notification_crud.unread_count(user_id=current_user.id)
    )


@fast_api_notification_router.post(
    "/user/me/notifications/read-all",
    status_code=status.HTTP_204_NO_CONTENT,
    description="Mark all of the current user's notifications as read.",
)
async def mark_all_my_notifications_read(
    current_user: User = Security(get_current_user),
    notification_crud: NotificationCRUD = Depends(NotificationCRUD.get_crud),
):
    await notification_crud.mark_all_read(user_id=current_user.id)


@fast_api_notification_router.post(
    "/user/me/notifications/{notification_id}/read",
    response_model=NotificationRead,
    description="Mark a single notification read. 404 if it is not the caller's.",
)
async def mark_my_notification_read(
    notification_id: uuid.UUID,
    current_user: User = Security(get_current_user),
    notification_crud: NotificationCRUD = Depends(NotificationCRUD.get_crud),
) -> NotificationRead:
    noti = await notification_crud.mark_read(
        notification_id=notification_id, user_id=current_user.id
    )
    if noti is None:
        # Same 404 whether it doesn't exist or belongs to someone else — never
        # reveal another user's notification ids.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such notification.",
        )
    return _to_read(noti)
