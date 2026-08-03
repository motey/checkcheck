"""A browser/device's Web Push subscription (chunk P1 of the system-notifications
plan, ``docs/plans/SYSTEM_NOTIFICATIONS.md`` section 3.1).

One row per subscribed device. Unlike ``UserNotificationSettings.webhook_url``
(one per user), a user can have several devices subscribed at once, so this is
its own table with ``user_id`` as a plain foreign key rather than the primary
key.

The three subscription fields (``endpoint``, ``p256dh``, ``auth``) are exactly
what the browser's ``PushSubscription.toJSON()`` returns, and exactly what
``pywebpush`` needs to encrypt a message for this one device
(``notify/push.py``). None of them is a secret in the sense the rest of this
codebase uses that word (an API token, a password): they identify *where* to
push and *how to encrypt for that endpoint*, not who is allowed to do it, and a
leaked endpoint alone still requires the encryption keys to be usable.

Like the outbox and the notification-settings table this is a delivery-plumbing
table, not a syncable entity: no client reads it through the delta feed.
"""

import datetime
import uuid
from typing import Optional

from sqlmodel import Field

from checkcheckserver.model._base_model import TimestampedModel, naive_utc_now


class PushSubscription(TimestampedModel, table=True):
    __tablename__ = "push_subscription"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
        unique=True,
    )
    user_id: uuid.UUID = Field(
        foreign_key="user.id",
        ondelete="CASCADE",
        index=True,
        description="Who this device belongs to. Deleting the account drops it.",
    )
    endpoint: str = Field(
        max_length=1024,
        unique=True,
        index=True,
        description=(
            "The push service URL for this one subscription. Already encodes both "
            "the push service and a per-subscription token, which is what makes it "
            "globally unique: re-subscribing the same browser is an upsert on this "
            "column, not a new row."
        ),
    )
    p256dh: str = Field(
        max_length=255,
        description="The subscription's public key (base64url), for payload encryption.",
    )
    auth: str = Field(
        max_length=255,
        description="The subscription's auth secret (base64url), for payload encryption.",
    )
    user_agent: Optional[str] = Field(
        default=None,
        max_length=512,
        description=(
            "Snapshot of the browser's user agent at subscribe time, for labelling "
            "this device in the settings UI's device list. Purely cosmetic."
        ),
    )
    last_seen_at: datetime.datetime = Field(
        default_factory=naive_utc_now,
        description=(
            "Naive UTC. Bumped on every successful delivery to this subscription, "
            "and on re-subscription. Not currently used to expire anything; kept "
            "for an operator inspecting the table and for a future cleanup pass."
        ),
    )
