"""CRUD for in-app notifications (Phase 9 of card sharing).

``emit_notification`` is the **single internal seam** the plan calls out: every
notification row is created here, so the "notification transports" sub-project
fans the same event out to email without touching any caller. It persists the
row, pushes a lightweight ``upd_prop="notification"`` over the existing SSE so a
connected client refreshes its feed/badge live, and queues whatever mail (chunk
E4) and whatever webhook (chunk E6) the recipient's preferences ask for.

The fan-out at the bottom of this module is where a notification meets the
preference resolver. Its rules, all from section 4.1 of
``docs/plans/EMAIL_NOTIFICATIONS.md``:

* Every channel is asked separately, and ``off`` means nothing happens on it.
  That includes ``in_app``: a user who muted the bell for a type gets no row at
  all, so it stays out of their feed, their badge and the delta feed.
* A recipient without an email address produces no outbox row, not a row that
  fails later. Same for an unverified address while
  ``NOTIFY_EMAIL_REQUIRE_VERIFIED`` is on.
* ``NOTIFY_EMAIL_MAX_PER_USER_PER_HOUR`` is a backstop: over the limit, the mail
  is dropped rather than queued, since a queue that keeps growing would deliver
  the flood later instead of preventing it.
* The webhook channel follows the same resolver but none of the mail-shaped
  rules: no address to be missing, no suppression delay, no coalescing and no
  hourly cap. A user without a stored URL simply produces no row.
* The push channel (chunk P1) is the same shape as the webhook minus the
  fixed target: no address to be missing, no suppression delay, no
  coalescing, but it does keep an hourly cap. A user with no subscribed
  device simply produces no row; the fan-out across whichever devices *are*
  subscribed happens at delivery time in ``notify/outbox.py``, not here.
* Nothing here sends anything. Queuing is all it does, and it never raises at a
  caller: a share must not fail because a message could not be rendered.
"""

import datetime
import uuid
from typing import List, Optional

from sqlmodel import delete, select, update, and_, col, func

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.db.sync_notification import SyncNotifiationCRUD
from checkcheckserver.db import push_subscription
from checkcheckserver.db.user_notification_settings import (
    get_or_create_settings,
    get_settings,
)
from checkcheckserver.model.sync_notifications import SyncNotification
from checkcheckserver.model.notification import (
    Notification,
    NotificationCreate,
    NotificationType,
)
from checkcheckserver.model.notification_outbox import NotificationChannel
from checkcheckserver.model.user import User
from checkcheckserver.model.user_notification_settings import UserNotificationSettings
from checkcheckserver.notify import outbox, render, schedule, unsubscribe
from checkcheckserver.notify.prefs import (
    NotificationMode,
    PreferenceChannel,
    resolve_mode,
)


log = get_logger()
config = Config()


def _utcnow() -> datetime.datetime:
    # Naive UTC to match the timestamps stored on the model (see TimestampedModel).
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _default_config() -> Config:
    """The module-level ``Config``, read at call time.

    Through ``globals()`` rather than the name directly because
    ``emit_notification`` takes a parameter called ``config`` that shadows it,
    and because a test that substitutes the module attribute has to be honoured.
    """
    return globals()["config"]


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


async def prune_feed_once(
    session,
    *,
    config: Optional[Config] = None,
    now: Optional[datetime.datetime] = None,
) -> int:
    """Delete old, already-read notifications. Returns the row count (chunk E6).

    The in-app feed has grown forever until now: every share, invite and opened
    link has left a row that nothing removes. ``NOTIFY_FEED_RETENTION_DAYS`` puts
    a bound on it, and ``0`` keeps everything.

    **Only rows the user has read** are pruned, which is what the setting says
    and what makes this safe to run unattended: an unread notification is still
    somebody's inbox, however old it is, and deleting it would silently drop the
    badge that is the only sign the event ever happened. The trade-off is that an
    account that never opens the bell still accumulates rows; that is a bounded
    number of events per card, and the feed endpoint reads a limited window
    anyway.

    Deleting a row also drops any outbox delivery still pointing at it (the
    foreign key cascades), which is the right outcome: nothing that old is
    waiting to be sent, and a message about a notification that no longer exists
    would only be confusing.
    """
    config = config or _default_config()
    days = config.NOTIFY_FEED_RETENTION_DAYS
    if days <= 0:
        return 0
    now = now or _utcnow()
    cutoff = now - datetime.timedelta(days=days)
    statement = (
        delete(Notification)
        .where(col(Notification.read_at).is_not(None))
        .where(col(Notification.created_at) < cutoff)
    )
    removed = (await session.exec(statement)).rowcount
    await session.commit()
    if removed:
        log.debug("[notify] pruned %s read notifications older than %s", removed, cutoff)
    return removed


async def emit_notification(
    notification_crud: NotificationCRUD,
    sync_crud: SyncNotifiationCRUD,
    *,
    user_id: uuid.UUID,
    type: NotificationType,
    cl_id: uuid.UUID,
    payload: Optional[dict] = None,
    config: Optional[Config] = None,
) -> Optional[Notification]:
    """Deliver one notification to every channel the recipient wants it on.

    This is the one place a ``Notification`` is created, and the one place mail
    is queued for one. ``cl_id`` is required because every notification refers to
    a card and the SSE envelope (``SyncNotification.cl_id``) is non-nullable; the
    push is pinned to ``user_id`` so only the recipient's connected clients
    refresh.

    Returns the created row, or **None** when the recipient has the ``in_app``
    channel switched off for this type: the matrix offers that choice, and the
    only honest way to honour it is to not write the row, since a row that exists
    shows up in the bell, the badge and the delta feed however it was flagged. No
    caller uses the return value; it is there for tests and for a future caller
    that wants to know.

    ``config`` is an injection seam for tests (an instance with mail off, an
    administrator's cap); production leaves it unset.
    """
    cfg = config or _default_config()
    session = notification_crud.session
    type_value = type.value if isinstance(type, NotificationType) else str(type)
    settings = await get_settings(session, user_id)

    noti: Optional[Notification] = None
    if (
        resolve_mode(settings, type_value, PreferenceChannel.in_app, config=cfg)
        == NotificationMode.off
    ):
        log.debug(
            "[notify] user %s has in-app '%s' switched off, no feed entry",
            user_id,
            type_value,
        )
    else:
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

    try:
        await _queue_email(
            session,
            user_id=user_id,
            type=type_value,
            cl_id=cl_id,
            payload=payload,
            notification=noti,
            settings=settings,
            config=cfg,
        )
        await _queue_webhook(
            session,
            user_id=user_id,
            type=type_value,
            cl_id=cl_id,
            payload=payload,
            notification=noti,
            settings=settings,
            config=cfg,
        )
        await _queue_push(
            session,
            user_id=user_id,
            type=type_value,
            cl_id=cl_id,
            payload=payload,
            notification=noti,
            settings=settings,
            config=cfg,
        )
    except Exception:
        # A share, an invite or a public-link open must not fail because a
        # message could not be queued. The notification itself is already
        # committed at this point, so the user still learns about it in the app.
        log.exception(
            "[notify] could not queue delivery for the '%s' notification to user %s",
            type_value,
            user_id,
        )
        # Leave the session usable for the rest of the request: a half-flushed
        # outbox row would otherwise blow up the caller's next statement.
        await session.rollback()
    return noti


async def _queue_email(
    session,
    *,
    user_id: uuid.UUID,
    type: str,
    cl_id: Optional[uuid.UUID],
    payload: Optional[dict],
    notification: Optional[Notification],
    settings: Optional[UserNotificationSettings],
    config: Config,
) -> None:
    """Queue at most one message for this notification, or explain why not."""
    mode = resolve_mode(settings, type, PreferenceChannel.email, config=config)
    if mode == NotificationMode.off:
        return

    user = await session.get(User, user_id)
    if user is None or not user.email:
        # Section 4.1.4: an ordinary outcome, not an error. OIDC accounts can
        # arrive without an email claim.
        log.debug("[notify] user %s has no email address, queueing nothing", user_id)
        return
    if config.NOTIFY_EMAIL_REQUIRE_VERIFIED and not user.is_email_verified:
        log.debug(
            "[notify] user %s has an unverified address, queueing nothing", user_id
        )
        return

    now = _utcnow()
    cap = config.NOTIFY_EMAIL_MAX_PER_USER_PER_HOUR
    if cap > 0:
        recent = await outbox.count_recent_for_user(
            session,
            user_id=user_id,
            channel=NotificationChannel.email,
            since=now - datetime.timedelta(hours=1),
        )
        if recent >= cap:
            log.warning(
                "[notify] user %s is over the hourly mail limit (%s), dropping a "
                "'%s' message",
                user_id,
                cap,
                type,
            )
            return

    # The unsubscribe link is signed with the user's own secret, which only
    # exists once they have a settings row, so this is the one path that creates
    # one for a user who never opened the settings dialog.
    if settings is None:
        settings = await get_or_create_settings(session, user_id)

    if mode in (NotificationMode.hourly, NotificationMode.daily):
        not_before = schedule.digest_due_at(
            mode, now=now, timezone_name=settings.timezone
        )
        dedupe_key = schedule.digest_dedupe_key(
            user_id=user_id, mode=mode, due_at=not_before
        )
        digest = mode.value
    else:
        not_before = schedule.immediate_due_at(now, config)
        dedupe_key = schedule.immediate_dedupe_key(
            user_id=user_id, type=type, actor_id=(payload or {}).get("actor_id")
        )
        digest = None

    context = render.notification_context(
        type=type,
        notification_id=notification.id if notification else None,
        cl_id=cl_id,
        payload=payload,
        created_at=now,
        digest=digest,
    )
    message = render.render_email(
        [context],
        to=user.email,
        recipient_name=user.display_name or user.user_name,
        unsubscribe_url=unsubscribe.unsubscribe_url(
            user_id=user_id,
            type=type,
            secret=settings.unsubscribe_secret,
            config=config,
            now=now,
        ),
        config=config,
    )
    await outbox.enqueue(
        session,
        user_id=user_id,
        channel=NotificationChannel.email,
        payload=message,
        notification_id=notification.id if notification else None,
        not_before=not_before,
        dedupe_key=dedupe_key,
        # The row joins the caller's transaction and is committed here, once,
        # together with anything else the request has pending.
        commit=False,
    )
    await session.commit()


async def _queue_webhook(
    session,
    *,
    user_id: uuid.UUID,
    type: str,
    cl_id: Optional[uuid.UUID],
    payload: Optional[dict],
    notification: Optional[Notification],
    settings: Optional[UserNotificationSettings],
    config: Config,
) -> None:
    """Queue the same notification for the user's own endpoint (chunk E6).

    Three differences from the mail path above, all of them because the
    recipient is a program rather than a person:

    * **No delay and no coalescing.** The webhook channel only offers ``off`` and
      ``immediate``, so the row is due now and carries no dedupe key: a receiver
      wants one request per event, not a summary of five.
    * **No hourly cap.** ``NOTIFY_EMAIL_MAX_PER_USER_PER_HOUR`` protects an inbox
      somebody has to read; a user pointing a webhook at their own service has
      already decided how much traffic they want.
    * **No unsubscribe link.** There is nowhere to put one, and the same user
      owns both ends.

    The URL is snapshotted into the row. A user who changes or clears it in the
    meantime does not redirect messages that were already addressed.
    """
    mode = resolve_mode(settings, type, PreferenceChannel.webhook, config=config)
    if mode == NotificationMode.off:
        return
    # `resolve_mode` already returns `off` when the instance has webhooks
    # switched off, so reaching here means the channel is live and the user
    # opted in. What can still be missing is the target itself.
    if settings is None or not settings.webhook_url:
        log.debug("[notify] user %s has no webhook URL, queueing nothing", user_id)
        return

    now = _utcnow()
    context = render.notification_context(
        type=type,
        notification_id=notification.id if notification else None,
        cl_id=cl_id,
        payload=payload,
        created_at=now,
    )
    await outbox.enqueue(
        session,
        user_id=user_id,
        channel=NotificationChannel.webhook,
        payload={
            "url": settings.webhook_url,
            "body": render.webhook_body(context, config),
        },
        notification_id=notification.id if notification else None,
        not_before=now,
        commit=False,
    )
    await session.commit()


async def _queue_push(
    session,
    *,
    user_id: uuid.UUID,
    type: str,
    cl_id: Optional[uuid.UUID],
    payload: Optional[dict],
    notification: Optional[Notification],
    settings: Optional[UserNotificationSettings],
    config: Config,
) -> None:
    """Queue one push delivery (chunk P1 of docs/plans/SYSTEM_NOTIFICATIONS.md).

    Unlike mail and the webhook, the row carries no fixed target: fan-out over
    the recipient's ``push_subscription`` rows happens in ``notify/outbox.py``
    at *delivery* time, not here. That is what lets a device subscribed after
    this row is queued but before it comes due still be reached, the push
    twin of "a subscription is not read again after it changes" simply
    inverted (plan section 4, "no subscription, no send").

    Like the webhook: no delay (the push channel only offers ``off`` and
    ``immediate``) and no coalescing, but it does keep the hourly cap, because
    a phone buzzing repeatedly is worse than a full inbox, unlike a receiver
    that decided its own appetite for traffic.
    """
    mode = resolve_mode(settings, type, PreferenceChannel.push, config=config)
    if mode == NotificationMode.off:
        return
    # `resolve_mode` already returns `off` when the instance has push switched
    # off, so reaching here means the channel is live. What can still be
    # missing is a subscribed device.
    if await push_subscription.count_for_user(session, user_id) == 0:
        log.debug(
            "[notify] user %s has no push subscription, queueing nothing", user_id
        )
        return

    now = _utcnow()
    cap = config.NOTIFY_PUSH_MAX_PER_USER_PER_HOUR
    if cap > 0:
        recent = await outbox.count_recent_for_user(
            session,
            user_id=user_id,
            channel=NotificationChannel.push,
            since=now - datetime.timedelta(hours=1),
        )
        if recent >= cap:
            log.warning(
                "[notify] user %s is over the hourly push limit (%s), dropping a "
                "'%s' push",
                user_id,
                cap,
                type,
            )
            return

    context = render.notification_context(
        type=type,
        notification_id=notification.id if notification else None,
        cl_id=cl_id,
        payload=payload,
        created_at=now,
    )
    # The same key `notify/schedule.py` computes for email coalescing (same
    # recipient, type and actor), reused here only as the OS-level `tag` that
    # collapses several system notifications about the same thing into one:
    # this row is never coalesced with another at the outbox level, since it
    # carries no dedupe_key of its own.
    tag = schedule.immediate_dedupe_key(
        user_id=user_id, type=type, actor_id=(payload or {}).get("actor_id")
    )
    await outbox.enqueue(
        session,
        user_id=user_id,
        channel=NotificationChannel.push,
        payload=render.push_payload(context, config, tag=tag),
        notification_id=notification.id if notification else None,
        not_before=now,
        commit=False,
    )
    await session.commit()
