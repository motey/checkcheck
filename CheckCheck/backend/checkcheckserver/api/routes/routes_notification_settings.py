"""Notification settings API (self-service, ``/user/me/notification-settings``).

Chunk E2 puts a single endpoint here, the test mail, so the whole delivery path
(enqueue, dispatcher, transport) can be exercised by hand on a real instance
from the day it exists. The preference matrix itself (``GET`` and ``PUT`` on
``/user/me/notification-settings``) arrives in chunk E3 and belongs in this same
module.

This router also carries the dispatcher's lifespan: FastAPI merges a router's
``lifespan_context`` into the app's, which is how the SSE listener starts its
background tasks too (``routes_sync_notification.py``).
"""

import datetime
import html
import uuid

from fastapi import APIRouter, Depends, HTTPException, Security, status
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from checkcheckserver.api.auth.security import get_current_user
from checkcheckserver.config import Config
from checkcheckserver.db._session import get_async_session
from checkcheckserver.db.user import User
from checkcheckserver.log import get_logger
from checkcheckserver.model._base_model import naive_utc_now
from checkcheckserver.model.notification_outbox import NotificationChannel
from checkcheckserver.notify import outbox
from checkcheckserver.notify.dispatcher import lifespan as dispatcher_lifespan
from checkcheckserver.notify.dispatcher import nudge


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
