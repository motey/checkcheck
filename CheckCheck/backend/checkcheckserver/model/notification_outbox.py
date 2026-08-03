"""Durable delivery queue for notifications (chunk E2 of the notification plan).

One row per (notification, channel) delivery. A producer writes a row and is
done; the background dispatcher (``notify/dispatcher.py``) picks due rows up,
hands them to a transport and records the outcome. That indirection is what
makes delivery survive a restart, retry a mail server that is briefly down, and
stay out of the request path.

Why the message is **snapshotted into ``payload``** rather than re-read at send
time: between enqueue and delivery the card may have been renamed, unshared or
tombstoned, so re-reading it in the dispatcher would either leak state the
recipient may no longer see or crash on a missing row. The snapshot is taken at
enqueue, right after the access check passed.

The table is a delivery *work queue*, not a syncable entity: no client ever sees
it and the delta feed does not read it. It inherits ``TimestampedModel`` for the
naive-UTC ``created_at`` / ``updated_at`` convention (its ``server_seq`` column
is stamped on insert like any other row, and never queried).

See ``docs/plans/EMAIL_NOTIFICATIONS.md`` section 3.2.
"""

import datetime
import enum
import uuid
from typing import Optional

from sqlalchemy import Index
from sqlmodel import Column, Field, JSON, String

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model._base_model import TimestampedModel, naive_utc_now


log = get_logger()
config = Config()


class NotificationChannel(str, enum.Enum):
    """Where a queued message is delivered (enum-as-string, like the rest)."""

    email = "email"
    webhook = "webhook"  # a JSON POST to the user's own endpoint (chunk E6)
    push = "push"  # Web Push to a subscribed browser/PWA (chunk P1)


class NotificationOutboxStatus(str, enum.Enum):
    """Lifecycle of one outbox row.

    ``pending``
        Waiting for its ``not_before`` to pass, or between retries.
    ``sent``
        Handed to a transport without error. Pruned after
        ``NOTIFY_OUTBOX_RETENTION_DAYS``.
    ``failed``
        Given up on: a permanent rejection, or ``NOTIFY_MAX_ATTEMPTS`` transient
        ones. Kept longer than ``sent`` so an operator can see what broke.
    ``cancelled``
        Deliberately not sent, and not an error: the recipient had already read
        the notification in the app before the mail became due.
    """

    pending = "pending"
    sent = "sent"
    failed = "failed"
    cancelled = "cancelled"


class NotificationOutbox(TimestampedModel, table=True):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        # The dispatcher's only hot query: "which rows are due now?".
        Index("ix_notification_outbox_status_not_before", "status", "not_before"),
    )

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
        description="The recipient. Deleting the user drops their queued mail.",
    )
    notification_id: Optional[uuid.UUID] = Field(
        default=None,
        foreign_key="notification.id",
        ondelete="CASCADE",
        index=True,
        description=(
            "The feed entry this delivery belongs to, when there is one. Null for "
            "mail that is not a notification (the test mail, and the public-link "
            "invitation of chunk E6)."
        ),
    )
    channel: NotificationChannel = Field(
        sa_type=String,
        description="Which transport delivers this row (enum-as-string).",
    )
    status: NotificationOutboxStatus = Field(
        default=NotificationOutboxStatus.pending,
        sa_type=String,
        description="Lifecycle state (enum-as-string).",
    )
    not_before: datetime.datetime = Field(
        default_factory=naive_utc_now,
        description=(
            "Naive UTC. The row is invisible to the dispatcher until this time, "
            "which carries both the suppress/coalesce delay and the retry backoff."
        ),
    )
    attempts: int = Field(
        default=0,
        description="Delivery attempts started so far. Bumped as the row is claimed.",
    )
    last_error: Optional[str] = Field(
        default=None,
        description="Why the last attempt failed. Operator-facing, never shown to a user.",
    )
    dedupe_key: Optional[str] = Field(
        default=None,
        index=True,
        description=(
            "Groups rows that should collapse into one message (chunk E4), and "
            "identifies a producer's own rows for rate limiting."
        ),
    )
    payload: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
        description=(
            "Everything the transport needs, snapshotted at enqueue time: the "
            "recipient address, subject and bodies. Never holds secrets."
        ),
    )
