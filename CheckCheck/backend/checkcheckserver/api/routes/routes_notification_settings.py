"""Notification settings API (self-service, ``/user/me/notification-settings``).

Nine endpoints:

* ``GET`` returns the **effective** preference matrix, which is what the user's
  own choices, the instance defaults and the administrator's caps add up to,
  together with enough detail for the settings dialog to explain itself (what
  each entry would fall back to, which entries are locked and why).
* ``PUT`` writes a partial patch of that matrix, plus the time zone and the
  webhook target.
* ``POST .../test-email`` queues a message to the caller's own address,
  ``POST .../test-webhook`` a request to their own endpoint, and
  ``POST .../test-push`` a push to every one of their own subscribed devices,
  so any delivery path can be checked by hand on a real instance.
* ``POST``/``GET``/``DELETE .../push-subscriptions`` register, list and remove
  a browser or installed PWA's Web Push subscription (chunk P1 of
  ``docs/plans/SYSTEM_NOTIFICATIONS.md``). Unlike the webhook target, a user
  can have several devices, so these are their own small table rather than a
  field on the settings row.
* ``GET`` and ``POST /notifications/unsubscribe`` are the link at the bottom of
  every message. They are **public**: a mail client has no session. See the
  section further down for why the acting half is the ``POST``.

The resolution rules live in ``notify/prefs.py``, not here; this module is the
HTTP shape around them, and the place where a rejected write becomes a status
code (400 for something that does not exist, 409 for something an administrator
has locked).

This router also carries the dispatcher's lifespan: FastAPI merges a router's
``lifespan_context`` into the app's, which is how the SSE listener starts its
background tasks too (``routes_sync_notification.py``).
"""

import datetime
import uuid
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from checkcheckserver.api.auth.security import get_current_user
from checkcheckserver.config import Config
from checkcheckserver.db import push_subscription as push_subscription_db
from checkcheckserver.db._session import get_async_session
from checkcheckserver.db.user import User
from checkcheckserver.db.user_notification_settings import (
    get_or_create_settings,
    get_settings,
)
from checkcheckserver.log import get_logger
from checkcheckserver.model._base_model import naive_utc_now
from checkcheckserver.model.notification_outbox import NotificationChannel
from checkcheckserver.model.user_notification_settings import UserNotificationSettings
from checkcheckserver.notify import branding, outbox, prefs, templating, unsubscribe
from checkcheckserver.notify.dispatcher import lifespan as dispatcher_lifespan
from checkcheckserver.notify.dispatcher import nudge
from checkcheckserver.notify.prefs import (
    LockedPreferenceError,
    NotificationMode,
    PreferenceChannel,
    UnknownPreferenceError,
)
from checkcheckserver.notify.unsubscribe import InvalidUnsubscribeToken


config = Config()
log = get_logger()

fast_api_notification_settings_router: APIRouter = APIRouter()

# The dispatcher runs for the whole process, not per request; hanging it on this
# router keeps it next to the endpoint that first fills the queue.
fast_api_notification_settings_router.lifespan_context = dispatcher_lifespan

# One test message per user per minute, per channel. Enforced against the outbox
# rather than an in-memory counter, so a restart does not hand out a fresh
# allowance and a second replica would share the same limit.
TEST_EMAIL_RATE_LIMIT_SECONDS = 60


def test_email_dedupe_key(user_id: uuid.UUID) -> str:
    return f"{user_id}:test_email"


def test_webhook_dedupe_key(user_id: uuid.UUID) -> str:
    return f"{user_id}:test_webhook"


# ── the preference matrix ─────────────────────────────────────────────────────


class NotificationChannelSetting(BaseModel):
    """One cell of the matrix: what happens for this type on this channel."""

    mode: NotificationMode = Field(
        description="The mode actually in force, after user choice and admin caps."
    )
    user_choice: Optional[NotificationMode] = Field(
        default=None,
        description=(
            "What the user explicitly picked here, or null when the entry is "
            "inherited. Null and a value equal to `default_mode` look the same "
            "today but behave differently once an administrator changes the "
            "default, so the dialog should show the difference."
        ),
    )
    default_mode: NotificationMode = Field(
        description="What this entry falls back to when the user has picked nothing."
    )
    locked: bool = Field(
        description="True when the administrator decided this, not the user."
    )
    locked_reason: Optional[str] = Field(
        default=None,
        description="Plain-language reason to show next to a locked entry.",
    )
    allowed_modes: List[NotificationMode] = Field(
        description=(
            "The modes this entry accepts. A digest is email-only, and some types "
            "narrow it further: `reminder_due` offers only `off` and `immediate` on "
            "email, because a reminder held back for a digest is not a reminder."
        )
    )
    mode_restriction_reason: Optional[str] = Field(
        default=None,
        description=(
            "Plain-language reason why this type offers fewer modes than its "
            "channel otherwise would, or null when it offers all of them. Unlike "
            "`locked_reason` this is a property of the notification type, not "
            "something an administrator configured."
        ),
    )


class NotificationTypeSettings(BaseModel):
    type: str = Field(description="Notification type, e.g. `card_shared`.")
    channels: Dict[str, NotificationChannelSetting] = Field(
        description="One entry per channel: `in_app`, `email`, `webhook`."
    )


class NotificationSettings(BaseModel):
    """Everything the settings dialog needs in one response."""

    types: List[NotificationTypeSettings] = Field(
        description="The full matrix, one entry per notification type the server knows."
    )
    timezone: Optional[str] = Field(
        default=None,
        description="The user's IANA time zone, or null to use UTC.",
    )
    webhook_url: Optional[str] = Field(
        default=None,
        description=(
            "Where this user's notification webhooks are POSTed, or null. Only "
            "called for types whose webhook mode is not `off`."
        ),
    )
    email_enabled: bool = Field(
        description="Whether this instance sends email at all. When false, every "
        "email entry is locked off."
    )
    webhook_enabled: bool = Field(
        description="Whether this instance allows per-user webhooks at all."
    )
    push_enabled: bool = Field(
        description="Whether this instance can send push notifications at all."
    )


class NotificationSettingsUpdate(BaseModel):
    """A partial update. Anything not named here keeps its stored value.

    ``prefs`` is merged entry by entry, so sending one type with one channel
    changes exactly that cell. A **null mode** removes the user's choice for that
    cell, which puts it back to inheriting the instance default; that is always
    allowed, even for an entry an administrator has locked, since it only ever
    drops an override.

    ``timezone`` and ``webhook_url`` follow the same rule at field level: leaving
    the field out keeps the stored value, sending null clears it.
    """

    prefs: Optional[Dict[str, Dict[str, Optional[str]]]] = Field(
        default=None,
        description=(
            "Partial matrix as {type: {channel: mode}}. Modes are `off`, "
            "`immediate`, `hourly` and `daily`; `in_app` and `webhook` accept only "
            "`off` and `immediate`, and `reminder_due` accepts only those two on "
            "email as well. A null mode restores the instance default for that "
            "entry."
        ),
        examples=[{"card_shared": {"email": "daily"}}],
    )
    timezone: Optional[str] = Field(
        default=None,
        description="IANA time zone name such as `Europe/Berlin`. Null clears it.",
        examples=["Europe/Berlin"],
    )
    webhook_url: Optional[str] = Field(
        default=None,
        description=(
            "Where this user's webhooks are POSTed. Null clears it. Rejected while "
            "the instance has webhooks switched off. Whether the URL may actually be "
            "called is decided again at delivery time, since a host name's address "
            "can change in between."
        ),
    )


def _settings_response(
    settings: Optional[UserNotificationSettings], cfg: Config
) -> NotificationSettings:
    """Render the effective matrix for a user, with or without a stored row."""
    types = []
    for type in prefs.known_types():
        channels = {}
        for channel in PreferenceChannel:
            reason = prefs.lock_reason(type, channel, cfg)
            channels[channel.value] = NotificationChannelSetting(
                mode=prefs.resolve_mode(settings, type, channel, config=cfg),
                user_choice=prefs.user_choice(settings, type, channel),
                # What the entry would be without this user's choice, which is
                # what the dialog shows as "(default)".
                default_mode=prefs.resolve_mode(None, type, channel, config=cfg),
                locked=reason is not None,
                locked_reason=reason,
                allowed_modes=prefs.allowed_modes(channel, type),
                mode_restriction_reason=prefs.mode_restriction_reason(type, channel),
            )
        types.append(NotificationTypeSettings(type=type, channels=channels))
    return NotificationSettings(
        types=types,
        timezone=settings.timezone if settings else None,
        webhook_url=settings.webhook_url if settings else None,
        email_enabled=bool(cfg.EMAIL_ENABLED),
        webhook_enabled=bool(cfg.NOTIFY_WEBHOOK_ENABLED),
        push_enabled=bool(cfg.NOTIFY_PUSH_ENABLED),
    )


@fast_api_notification_settings_router.get(
    "/user/me/notification-settings",
    response_model=NotificationSettings,
    description=(
        "The current user's effective notification settings: for every notification "
        "type and channel the mode actually in force, what the user picked, what it "
        "would fall back to, and whether an administrator locked it. A user who has "
        "never saved anything gets the instance defaults; no row is created by "
        "reading."
    ),
)
async def get_notification_settings(
    current_user: User = Security(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> NotificationSettings:
    settings = await get_settings(session, current_user.id)
    return _settings_response(settings, config)


@fast_api_notification_settings_router.put(
    "/user/me/notification-settings",
    response_model=NotificationSettings,
    description=(
        "Update the current user's notification settings, partially: only the "
        "entries and fields present in the body change. Returns the full effective "
        "settings afterwards. Returns 400 for an unknown notification type, channel, "
        "mode or time zone, and 409 for an entry the administrator has locked."
    ),
)
async def update_notification_settings(
    update: NotificationSettingsUpdate,
    current_user: User = Security(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> NotificationSettings:
    provided = update.model_fields_set

    # Validate everything before touching the database, so a request that is
    # rejected halfway cannot leave half of itself stored.
    try:
        if update.prefs is not None:
            prefs.validate_prefs_patch(update.prefs, config=config)
        timezone = (
            prefs.validate_timezone(update.timezone)
            if update.timezone is not None
            else None
        )
        webhook_url = (
            prefs.validate_webhook_url(update.webhook_url)
            if update.webhook_url is not None
            else None
        )
    except LockedPreferenceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except UnknownPreferenceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if webhook_url is not None and not config.NOTIFY_WEBHOOK_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This server does not send webhooks.",
        )

    writes_something = bool(
        update.prefs or "timezone" in provided or "webhook_url" in provided
    )
    if not writes_something:
        # An empty body is not an error, and it must not conjure a row for a user
        # who has never chosen anything.
        return _settings_response(await get_settings(session, current_user.id), config)

    settings = await get_or_create_settings(session, current_user.id)
    if update.prefs is not None:
        # Assigned, never mutated in place: SQLAlchemy does not notice a change
        # made inside a JSON column's dict.
        settings.prefs = prefs.apply_prefs_patch(settings.prefs, update.prefs)
    if "timezone" in provided:
        settings.timezone = timezone
    if "webhook_url" in provided:
        settings.webhook_url = webhook_url

    session.add(settings)
    await session.commit()
    await session.refresh(settings)
    log.debug("[notify] updated notification settings for user %s", current_user.id)
    return _settings_response(settings, config)


# ── the test message ──────────────────────────────────────────────────────────


class TestEmailResult(BaseModel):
    queued_id: uuid.UUID = Field(
        description="Id of the queued delivery, for support and log correlation."
    )
    to: str = Field(description="The address it was queued for (the caller's own).")
    queued_at: datetime.datetime = Field(
        description="Naive UTC time the message entered the queue."
    )


@fast_api_notification_settings_router.post(
    "/user/me/notification-settings/test-email",
    response_model=TestEmailResult,
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Queue a test message to the current user's own email address, to check that "
        "the instance's mail setup works. The message is sent by the background "
        "dispatcher, so a 202 means queued, not delivered. Returns 409 when the "
        "instance has email switched off or the account has no address, and 429 at "
        "most once a minute."
    ),
)
async def send_test_email(
    current_user: User = Security(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> TestEmailResult:
    if not config.EMAIL_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This instance does not send email.",
        )
    if not current_user.email:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your account has no email address, so there is nowhere to send it.",
        )

    now = naive_utc_now()
    dedupe_key = test_email_dedupe_key(current_user.id)
    recent = await outbox.count_recent(
        session,
        user_id=current_user.id,
        dedupe_key=dedupe_key,
        since=now - datetime.timedelta(seconds=TEST_EMAIL_RATE_LIMIT_SECONDS),
    )
    if recent:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="A test message was already queued in the last minute.",
            headers={"Retry-After": str(TEST_EMAIL_RATE_LIMIT_SECONDS)},
        )

    row = await outbox.enqueue(
        session,
        user_id=current_user.id,
        channel=NotificationChannel.email,
        payload=_test_email_payload(current_user),
        dedupe_key=dedupe_key,
    )
    # Send it now rather than at the next tick: someone is sitting in front of
    # the settings dialog waiting for it.
    nudge()
    return TestEmailResult(queued_id=row.id, to=current_user.email, queued_at=row.created_at)


def _test_email_payload(user: User) -> dict:
    """The whole message, snapshotted the way every queued delivery is."""
    context = branding.brand_context(config)
    context["greeting"] = user.display_name or user.user_name
    return {
        "to": user.email,
        "subject": f"{config.APP_NAME} test message",
        "text_body": templating.render("test_email.txt", context, config=config),
        "html_body": templating.render("test_email.html", context, config=config),
    }


# ── the test webhook ──────────────────────────────────────────────────────────


class TestWebhookResult(BaseModel):
    queued_id: uuid.UUID = Field(
        description="Id of the queued delivery, for support and log correlation."
    )
    url: str = Field(description="The URL it was queued for (the caller's own).")
    queued_at: datetime.datetime = Field(
        description="Naive UTC time the message entered the queue."
    )


@fast_api_notification_settings_router.post(
    "/user/me/notification-settings/test-webhook",
    response_model=TestWebhookResult,
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Queue a test POST to the current user's own webhook URL, to check that it is "
        "reachable and that the guard against private addresses is not in the way. The "
        "request is made by the background dispatcher, so a 202 means queued, not "
        "delivered; a URL this server refuses to call fails in the queue and the reason "
        "is in the server log. Returns 409 when the instance has webhooks switched off "
        "or the account has no URL saved, and 429 at most once a minute."
    ),
)
async def send_test_webhook(
    current_user: User = Security(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> TestWebhookResult:
    if not config.NOTIFY_WEBHOOK_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This instance does not send webhooks.",
        )
    settings = await get_settings(session, current_user.id)
    if settings is None or not settings.webhook_url:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Save a webhook URL first, so there is somewhere to send it.",
        )

    now = naive_utc_now()
    dedupe_key = test_webhook_dedupe_key(current_user.id)
    recent = await outbox.count_recent(
        session,
        user_id=current_user.id,
        dedupe_key=dedupe_key,
        since=now - datetime.timedelta(seconds=TEST_EMAIL_RATE_LIMIT_SECONDS),
    )
    if recent:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="A test webhook was already queued in the last minute.",
            headers={"Retry-After": str(TEST_EMAIL_RATE_LIMIT_SECONDS)},
        )

    row = await outbox.enqueue(
        session,
        user_id=current_user.id,
        channel=NotificationChannel.webhook,
        payload={
            "url": settings.webhook_url,
            # Same shape as a real one, so a receiver written against this test
            # keeps working when the real events start arriving. `type` names it
            # as the test it is, rather than borrowing a notification type.
            "body": {
                "type": "test",
                "notification_id": None,
                "checklist_id": None,
                "checklist_name": None,
                "actor": None,
                "created_at": now.isoformat(),
                "url": (config.SERVER_PUBLIC_URL or "").rstrip("/") + "/",
                "app": config.APP_NAME,
                "text": f"This is a test webhook from {config.APP_NAME}.",
            },
        },
        dedupe_key=dedupe_key,
    )
    nudge()
    return TestWebhookResult(
        queued_id=row.id, url=settings.webhook_url, queued_at=row.created_at
    )


# ── push subscriptions ───────────────────────────────────────────────────────
#
# A user can have several devices, unlike the single webhook_url, so these are
# their own small table (``push_subscription``) rather than a field on the
# settings row. See docs/plans/SYSTEM_NOTIFICATIONS.md section 6.


class PushSubscriptionKeys(BaseModel):
    p256dh: str = Field(
        max_length=255, description="The subscription's public key (base64url)."
    )
    auth: str = Field(
        max_length=255, description="The subscription's auth secret (base64url)."
    )


class PushSubscriptionRegister(BaseModel):
    """The shape ``PushSubscription.toJSON()`` produces in the browser."""

    endpoint: str = Field(
        max_length=1024, description="The push service URL for this subscription."
    )
    keys: PushSubscriptionKeys
    user_agent: Optional[str] = Field(
        default=None,
        max_length=512,
        description="The browser's user agent, for labelling this device later.",
    )


class PushSubscriptionInfo(BaseModel):
    id: uuid.UUID
    endpoint: str = Field(
        description=(
            "The subscription's own endpoint URL, so the client can tell which row "
            "is 'this device' by comparing it to its own current subscription."
        )
    )
    user_agent: Optional[str] = None
    created_at: datetime.datetime
    last_seen_at: datetime.datetime


@fast_api_notification_settings_router.post(
    "/user/me/push-subscriptions",
    response_model=PushSubscriptionInfo,
    status_code=status.HTTP_201_CREATED,
    description=(
        "Register (or refresh) one browser or installed-PWA push subscription for the "
        "current user. Upserts on `endpoint`, so calling this again for the same device "
        "(the normal pattern: a page re-checks its subscription on every load) updates the "
        "existing row instead of creating a duplicate. Returns 409 while the instance has "
        "push switched off."
    ),
)
async def register_push_subscription(
    body: PushSubscriptionRegister,
    current_user: User = Security(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> PushSubscriptionInfo:
    if not config.NOTIFY_PUSH_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This instance does not send push notifications.",
        )
    row = await push_subscription_db.upsert(
        session,
        user_id=current_user.id,
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
        user_agent=body.user_agent,
    )
    log.debug(
        "[notify] registered push subscription %s for user %s", row.id, current_user.id
    )
    return PushSubscriptionInfo(
        id=row.id,
        endpoint=row.endpoint,
        user_agent=row.user_agent,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
    )


@fast_api_notification_settings_router.get(
    "/user/me/push-subscriptions",
    response_model=List[PushSubscriptionInfo],
    description="The current user's subscribed devices, for the settings dialog's device list.",
)
async def list_push_subscriptions(
    current_user: User = Security(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> List[PushSubscriptionInfo]:
    rows = await push_subscription_db.list_for_user(session, current_user.id)
    return [
        PushSubscriptionInfo(
            id=row.id,
            endpoint=row.endpoint,
            user_agent=row.user_agent,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
        )
        for row in rows
    ]


@fast_api_notification_settings_router.delete(
    "/user/me/push-subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    description=(
        "Remove one of the current user's push subscriptions, for example when they turn "
        "notifications off on that device. 404 when it does not exist or belongs to someone "
        "else."
    ),
)
async def delete_push_subscription(
    subscription_id: uuid.UUID,
    current_user: User = Security(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    removed = await push_subscription_db.delete_for_user(
        session, subscription_id=subscription_id, user_id=current_user.id
    )
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such push subscription."
        )
    log.debug(
        "[notify] removed push subscription %s for user %s",
        subscription_id,
        current_user.id,
    )


# ── the test push ────────────────────────────────────────────────────────────


def test_push_dedupe_key(user_id: uuid.UUID) -> str:
    return f"{user_id}:test_push"


class TestPushResult(BaseModel):
    queued_id: uuid.UUID = Field(
        description="Id of the queued delivery, for support and log correlation."
    )
    subscription_count: int = Field(
        description="How many of the caller's devices this will be pushed to."
    )
    queued_at: datetime.datetime = Field(
        description="Naive UTC time the message entered the queue."
    )


@fast_api_notification_settings_router.post(
    "/user/me/notification-settings/test-push",
    response_model=TestPushResult,
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Queue a test push to every one of the current user's subscribed devices, to check "
        "that the instance's push setup works. The message is sent by the background "
        "dispatcher, so a 202 means queued, not delivered. Returns 409 when the instance has "
        "push switched off or the account has no subscribed device, and 429 at most once a "
        "minute."
    ),
)
async def send_test_push(
    current_user: User = Security(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> TestPushResult:
    if not config.NOTIFY_PUSH_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This instance does not send push notifications.",
        )
    subscription_count = await push_subscription_db.count_for_user(session, current_user.id)
    if not subscription_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No device is subscribed yet, so there is nowhere to send it.",
        )

    now = naive_utc_now()
    dedupe_key = test_push_dedupe_key(current_user.id)
    recent = await outbox.count_recent(
        session,
        user_id=current_user.id,
        dedupe_key=dedupe_key,
        since=now - datetime.timedelta(seconds=TEST_EMAIL_RATE_LIMIT_SECONDS),
    )
    if recent:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="A test push was already queued in the last minute.",
            headers={"Retry-After": str(TEST_EMAIL_RATE_LIMIT_SECONDS)},
        )

    row = await outbox.enqueue(
        session,
        user_id=current_user.id,
        channel=NotificationChannel.push,
        payload={
            "title": f"{config.APP_NAME} test notification",
            "body": "This is a test push notification.",
            "url": (config.SERVER_PUBLIC_URL or "").rstrip("/") + "/",
            "tag": dedupe_key,
        },
        dedupe_key=dedupe_key,
    )
    nudge()
    return TestPushResult(
        queued_id=row.id, subscription_count=subscription_count, queued_at=row.created_at
    )


# ── unsubscribe ───────────────────────────────────────────────────────────────
#
# Public, sessionless, and split in two on purpose:
#
# * ``GET`` only *shows* what would happen, with a button. Mail security scanners
#   and link previewers fetch every URL in a message before the human sees it, so
#   a GET that acted would unsubscribe people who never clicked anything. Same
#   reasoning as decision 3 of the plan, where marking a notification read moved
#   out of a redirect endpoint and into the SPA.
# * ``POST`` acts. That is also exactly what RFC 8058 one-click asks for, so the
#   ``List-Unsubscribe-Post`` header on every message lets a mail client do it
#   directly, with no browser and no confirmation step.
#
# Every failure, whatever it was, produces the same neutral page: a probe must
# not be able to tell a forged token from an expired one, or learn whether an
# account exists.

# Wording for the notification type on the confirmation page. Falls back to the
# raw type name for anything a later release adds without touching this.
_TYPE_WORDING = {
    "card_shared": "cards being shared with you",
    "card_invited": "invitations to cards",
    "public_link_opened": "your public links being opened",
    "reminder_due": "reminders you set on cards",
}


def _unsubscribe_page(
    title: str,
    lead: str,
    detail: Optional[str] = None,
    *,
    form_action: Optional[str] = None,
    form_label: Optional[str] = None,
    status_code: int = 200,
) -> HTMLResponse:
    """A tiny self-contained page. No app assets: this is reached from an inbox,
    possibly on a device that has never loaded the client. Autoescaping in the
    template covers ``title``/``lead``/``detail``, all of which may echo a
    caller-controlled token; Jinja escapes them the same as any other value."""
    context = branding.brand_context(config)
    context.update(
        {
            "title": title,
            "lead": lead,
            "detail": detail,
            "form_action": form_action,
            "form_label": form_label,
        }
    )
    return HTMLResponse(
        status_code=status_code,
        content=templating.render("unsubscribe_page.html", context, config=config),
    )


def _unsubscribe_failed() -> HTMLResponse:
    return _unsubscribe_page(
        "This link is not valid",
        "This unsubscribe link is expired or was not issued by this server. You can "
        "change what you receive in your notification settings instead.",
        status_code=status.HTTP_400_BAD_REQUEST,
    )


async def _claims_for(
    session: AsyncSession, token: str
) -> "tuple[UserNotificationSettings, str]":
    """Verify *token* and return whose it is, or raise :class:`InvalidUnsubscribeToken`.

    Two steps, because the signing key is the user's own secret: read the
    (unverified) claims to find out which row to load, then check the signature
    against that row. A user with no settings row has no secret and therefore no
    token can ever have been minted for them.
    """
    asserted = unsubscribe.read_claims(token)
    settings = await get_settings(session, asserted.user_id)
    if settings is None or not settings.unsubscribe_secret:
        raise InvalidUnsubscribeToken("No such recipient.")
    claims = unsubscribe.verify_token(token, secret=settings.unsubscribe_secret)
    if claims.type not in prefs.known_types():
        raise InvalidUnsubscribeToken("Unknown notification type.")
    return settings, claims.type


@fast_api_notification_settings_router.get(
    "/notifications/unsubscribe",
    response_class=HTMLResponse,
    description=(
        "The unsubscribe link carried by every notification email. Shows what "
        "would be switched off and a button that does it; nothing changes on this "
        "request, so a mail scanner following the link cannot unsubscribe anybody. "
        "An invalid, forged or expired token gets the same neutral page as any "
        "other failure."
    ),
)
async def unsubscribe_confirm(
    token: str = Query(description="The signed token from the message."),
    session: AsyncSession = Depends(get_async_session),
) -> HTMLResponse:
    try:
        _settings, type = await _claims_for(session, token)
    except InvalidUnsubscribeToken as exc:
        log.debug("[notify] unsubscribe link rejected: %s", exc)
        return _unsubscribe_failed()

    wording = _TYPE_WORDING.get(type, type)
    return _unsubscribe_page(
        "Stop these emails?",
        f"You will no longer receive email about {wording}.",
        "Everything else, including the notifications inside the app, stays as it is.",
        form_action=f"{unsubscribe.UNSUBSCRIBE_PATH}?token={token}",
        form_label="Yes, stop these emails",
    )


@fast_api_notification_settings_router.post(
    "/notifications/unsubscribe",
    response_class=HTMLResponse,
    description=(
        "Switches the email channel off for exactly one notification type and one "
        "user, named by the signed token. Also the RFC 8058 one-click target "
        "advertised by the List-Unsubscribe-Post header, so a mail client can do it "
        "without opening a browser. Never touches any other type, any other "
        "channel, or anybody else's settings."
    ),
)
async def unsubscribe_apply(
    token: str = Query(description="The signed token from the message."),
    session: AsyncSession = Depends(get_async_session),
) -> HTMLResponse:
    try:
        settings, type = await _claims_for(session, token)
    except InvalidUnsubscribeToken as exc:
        log.debug("[notify] unsubscribe request rejected: %s", exc)
        return _unsubscribe_failed()

    # Deliberately not through `validate_prefs_patch`: switching a channel *off*
    # is always allowed, including for an entry an administrator has locked on,
    # and this path must keep working whatever the instance configuration is.
    settings.prefs = prefs.apply_prefs_patch(
        settings.prefs, {type: {PreferenceChannel.email.value: NotificationMode.off.value}}
    )
    session.add(settings)
    await session.commit()
    log.info("[notify] user %s unsubscribed from '%s' email", settings.user_id, type)

    wording = _TYPE_WORDING.get(type, type)
    return _unsubscribe_page(
        "Done",
        f"You will no longer receive email about {wording}.",
        "You can turn it back on any time in your notification settings.",
    )
