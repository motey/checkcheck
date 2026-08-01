"""Per-user notification preferences (chunk E3 of the notification plan).

One row per user, created lazily: a user who never opened the settings dialog has
no row at all and is served the instance defaults. That is deliberate, and it is
the same reason ``prefs`` is a single JSON blob rather than a column per
notification type: **adding a notification type or a channel must not require a
migration and must not require backfilling every user row**. A key that is not in
``prefs`` means "whatever the instance default says", resolved in code by
``notify/prefs.py``.

Shape of ``prefs``::

    {"<notification_type>": {"<channel>": "<mode>"}}
    {"card_shared": {"email": "immediate"}, "public_link_opened": {"email": "off"}}

Types are ``NotificationType`` members, channels are ``in_app``, ``email`` and
``webhook``, modes are ``off``, ``immediate``, ``hourly`` and ``daily`` (not every
channel accepts every mode, see ``notify/prefs.py``). Nothing consumes the
resolved modes yet; the fan-out in ``emit_notification`` arrives in chunk E4.

Like the outbox this is a settings table, not a syncable entity: no client sees it
through the delta feed. It inherits ``TimestampedModel`` for the naive-UTC
``created_at`` / ``updated_at`` convention.

See ``docs/plans/EMAIL_NOTIFICATIONS.md`` section 3.1.
"""

import secrets
import uuid
from typing import Optional

from sqlmodel import Column, Field, JSON

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model._base_model import TimestampedModel


log = get_logger()
config = Config()


def generate_unsubscribe_secret() -> str:
    """Per-user signing secret behind the one-click unsubscribe link (chunk E4).

    Per user rather than one instance-wide key so that rotating it (a user who
    thinks a link leaked) invalidates only that user's outstanding links, and so
    a forged token cannot be built from another user's leaked one.
    """
    return secrets.token_urlsafe(32)


class UserNotificationSettings(TimestampedModel, table=True):
    __tablename__ = "user_notification_settings"

    # The user *is* the identity of the row: one settings row per account, so the
    # foreign key doubles as the primary key and no separate uniqueness rule is
    # needed. Deleting the account takes the row with it.
    user_id: uuid.UUID = Field(
        primary_key=True,
        foreign_key="user.id",
        ondelete="CASCADE",
        nullable=False,
        description="The account these preferences belong to.",
    )
    prefs: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
        description=(
            "Chosen delivery mode per notification type and channel, as "
            '{"<type>": {"<channel>": "<mode>"}}. A missing key means the instance '
            "default applies, which is what lets a new notification type work "
            "without a migration."
        ),
    )
    timezone: Optional[str] = Field(
        default=None,
        max_length=64,
        description=(
            "IANA time zone name, for example 'Europe/Berlin'. Used for the "
            "timestamps in mail today and by the date-reminder feature later. Null "
            "means UTC."
        ),
    )
    webhook_url: Optional[str] = Field(
        default=None,
        max_length=2048,
        description=(
            "Where the webhook channel POSTs (chunk E6). Null means the user has no "
            "webhook. Stored here, but nothing calls it before E6."
        ),
    )
    unsubscribe_secret: str = Field(
        default_factory=generate_unsubscribe_secret,
        max_length=64,
        nullable=False,
        description=(
            "Signs this user's unsubscribe links (chunk E4). A secret: never "
            "returned by an API, never logged, never put in a payload."
        ),
    )
