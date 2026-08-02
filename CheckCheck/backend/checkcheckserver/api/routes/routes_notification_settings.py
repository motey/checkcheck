"""Notification settings API (self-service, ``/user/me/notification-settings``).

Five endpoints:

* ``GET`` returns the **effective** preference matrix, which is what the user's
  own choices, the instance defaults and the administrator's caps add up to,
  together with enough detail for the settings dialog to explain itself (what
  each entry would fall back to, which entries are locked and why).
* ``PUT`` writes a partial patch of that matrix, plus the time zone and the
  webhook target.
* ``POST .../test-email`` queues a message to the caller's own address, so the
  whole delivery path can be checked by hand on a real instance.
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
import html
import uuid
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from checkcheckserver.api.auth.security import get_current_user
from checkcheckserver.config import Config
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
from checkcheckserver.notify import outbox, prefs, unsubscribe
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

# One test mail per user per minute. Enforced against the outbox rather than an
# in-memory counter, so a restart does not hand out a fresh allowance and a
# second replica would share the same limit.
TEST_EMAIL_RATE_LIMIT_SECONDS = 60


def test_email_dedupe_key(user_id: uuid.UUID) -> str:
    return f"{user_id}:test_email"


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
        description="The modes this channel accepts. A digest is email-only."
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
        description="The user's webhook target, or null. Not called before chunk E6.",
    )
    email_enabled: bool = Field(
        description="Whether this instance sends email at all. When false, every "
        "email entry is locked off."
    )
    webhook_enabled: bool = Field(
        description="Whether this instance allows per-user webhooks at all."
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
            "`off` and `immediate`. A null mode restores the instance default for "
            "that entry."
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
            "Where this user's webhooks go (chunk E6). Null clears it. Rejected "
            "while the instance has webhooks switched off."
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
                allowed_modes=prefs.allowed_modes(channel),
            )
        types.append(NotificationTypeSettings(type=type, channels=channels))
    return NotificationSettings(
        types=types,
        timezone=settings.timezone if settings else None,
        webhook_url=settings.webhook_url if settings else None,
        email_enabled=bool(cfg.EMAIL_ENABLED),
        webhook_enabled=bool(cfg.NOTIFY_WEBHOOK_ENABLED),
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
    """The whole message, snapshotted the way every queued delivery is.

    Deliberately hand-written rather than routed through a template: there are
    no templates before chunk E4, and a test mail should say as little as
    possible about the instance beyond proving that delivery works.
    """
    app_name = config.APP_NAME
    greeting = user.display_name or user.user_name
    # A display name is user-controlled text going into an HTML body, so it is
    # escaped even though the recipient is its owner.
    greeting_html = html.escape(greeting)
    text_body = (
        f"Hello {greeting},\n\n"
        f"this is a test message from {app_name} at {config.SERVER_PUBLIC_URL}.\n"
        "If it reached you, the email setup of this instance works.\n\n"
        "Nobody else received a copy, and you can ignore this message.\n"
    )
    html_body = (
        f"<p>Hello {greeting_html},</p>"
        f"<p>this is a test message from <strong>{app_name}</strong> at "
        f'<a href="{config.SERVER_PUBLIC_URL}">{config.SERVER_PUBLIC_URL}</a>.<br>'
        "If it reached you, the email setup of this instance works.</p>"
        "<p>Nobody else received a copy, and you can ignore this message.</p>"
    )
    return {
        "to": user.email,
        "subject": f"{app_name} test message",
        "text_body": text_body,
        "html_body": html_body,
    }


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
}


def _unsubscribe_page(title: str, body: str, *, status_code: int = 200) -> HTMLResponse:
    """A tiny self-contained page. No app assets: this is reached from an inbox,
    possibly on a device that has never loaded the client."""
    app_name = html.escape(config.APP_NAME)
    public_url = html.escape((config.SERVER_PUBLIC_URL or "").rstrip("/") + "/", quote=True)
    return HTMLResponse(
        status_code=status_code,
        content=(
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex">'
            f"<title>{html.escape(title)} - {app_name}</title></head>"
            '<body style="font-family:system-ui,-apple-system,Segoe UI,Helvetica,Arial,'
            'sans-serif;line-height:1.5;color:#1f2328;max-width:34rem;margin:4rem auto;'
            'padding:0 1.5rem">'
            f"<h1 style=\"font-size:1.25rem\">{html.escape(title)}</h1>"
            f"{body}"
            f'<p style="font-size:.85rem;color:#59636e;margin-top:2rem">'
            f'<a href="{public_url}">Back to {app_name}</a></p>'
            "</body></html>"
        ),
    )


def _unsubscribe_failed() -> HTMLResponse:
    return _unsubscribe_page(
        "This link is not valid",
        "<p>This unsubscribe link is expired or was not issued by this server. "
        "You can change what you receive in your notification settings instead.</p>",
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

    wording = html.escape(_TYPE_WORDING.get(type, type))
    escaped_token = html.escape(token, quote=True)
    return _unsubscribe_page(
        "Stop these emails?",
        f"<p>You will no longer receive email about <strong>{wording}</strong>. "
        "Everything else, including the notifications inside the app, stays as it "
        "is.</p>"
        f'<form method="post" action="{unsubscribe.UNSUBSCRIBE_PATH}?token={escaped_token}">'
        '<button type="submit" style="font:inherit;padding:.6rem 1.1rem;border:0;'
        'border-radius:.4rem;background:#1f2328;color:#fff;cursor:pointer">'
        "Yes, stop these emails</button></form>",
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

    wording = html.escape(_TYPE_WORDING.get(type, type))
    return _unsubscribe_page(
        "Done",
        f"<p>You will no longer receive email about <strong>{wording}</strong>. "
        "You can turn it back on any time in your notification settings.</p>",
    )
