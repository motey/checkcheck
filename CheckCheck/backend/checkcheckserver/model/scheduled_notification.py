"""A reminder a user set on a card (chunk R1 of the date-reminder plan).

One row is one instruction: "tell me about this card at this time, and then
either stop or do it again next week". The dispatcher's per-tick scan
(``notify/reminders.py``, chunk R2) picks up due rows and emits a
``reminder_due`` notification through the single existing seam, so a reminder
inherits the whole notification stack for free: preferences, mail, webhooks,
retries and the deep link back into the card.

Two properties of this table are decisions rather than details, both from
``docs/plans/DATE_REMINDERS.md`` section 2:

**A reminder belongs to the user who set it, and only they are notified.** It is
personal, not a property of the card, which is why there is exactly one
``user_id`` and no audience concept. Any collaborator can set their own on the
same card, and nobody can schedule a notification for somebody else.

**The row carries its own ``timezone``**, snapshotted at creation from the user's
notification settings. Recurrence is computed in that zone, so "every day at
09:00" stays 09:00 across a DST change, and a user who later moves and changes
their global zone does not silently find every existing reminder shifted.

Like the outbox and the settings table this is **not** a syncable entity: no
client sees it through the delta feed, so it gets no ``SoftDeleteMixin``. A
reminder the user removes is a real ``DELETE``, not a tombstone. It inherits
``TimestampedModel`` for the naive-UTC ``created_at`` / ``updated_at`` convention.

See ``docs/plans/DATE_REMINDERS.md`` section 3.1.
"""

import datetime
import enum
import uuid
from typing import Optional

from sqlalchemy import Index
from sqlmodel import Field, String

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model._base_model import TimestampedModel


log = get_logger()
config = Config()


# The user's own text on a reminder ("call the plumber"). Bounded because it ends
# up in a mail subject line and in a webhook body.
REMINDER_NOTE_MAX_LENGTH = 200


class ReminderRecurrence(str, enum.Enum):
    """How often a reminder repeats (enum-as-string, like the rest).

    Deliberately three simple rules rather than an RRULE: "every second Tuesday"
    means adopting a calendar library, which is a decision on its own and not one
    a checklist app has earned yet. All three are computed in the row's own time
    zone, see ``notify/recurrence.py``.
    """

    none = "none"  # fires once, then the row is done
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"


class ReminderStatus(str, enum.Enum):
    """Lifecycle of one reminder row.

    ``pending``
        Waiting for ``remind_at``. A recurring reminder stays here forever,
        rolling its ``remind_at`` forward after each fire.
    ``done``
        A one-off that fired. Kept for a while so the user can see it happened,
        then pruned by the dispatcher's hourly housekeeping.
    ``cancelled``
        Will not fire: the fire-time check found the card tombstoned or the
        user's access gone (plan decision 2). Not an error, and deliberately
        silent, since it usually follows something the user did on purpose.
    """

    pending = "pending"
    done = "done"
    cancelled = "cancelled"


class ScheduledNotification(TimestampedModel, table=True):
    __tablename__ = "scheduled_notification"
    __table_args__ = (
        # The scan's only hot query: "which pending rows are due now?". It runs
        # every tick forever, so it gets the composite index rather than relying
        # on the single-column one below.
        Index("ix_scheduled_notification_status_remind_at", "status", "remind_at"),
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
        description=(
            "Who set this reminder, and the only person it notifies. Deleting the "
            "account drops their reminders."
        ),
    )
    cl_id: uuid.UUID = Field(
        foreign_key="checklist.id",
        ondelete="CASCADE",
        index=True,
        description=(
            "The card the reminder is about. Cards are tombstoned rather than "
            "deleted, so this cascade essentially never fires; a deleted card is "
            "caught by the fire-time check instead."
        ),
    )
    remind_at: datetime.datetime = Field(
        description=(
            "Naive UTC. The next (or only) time this fires. A recurring reminder "
            "rewrites it after every fire. Not indexed on its own: nothing ever "
            "asks about a time without also asking about a status, and the "
            "composite index above is what the scan uses."
        ),
    )
    timezone: Optional[str] = Field(
        default=None,
        max_length=64,
        description=(
            "IANA time zone name the recurrence is computed in, snapshotted at "
            "creation from the user's notification settings. Null means UTC."
        ),
    )
    recurrence: ReminderRecurrence = Field(
        default=ReminderRecurrence.none,
        sa_type=String,
        description="How often it repeats (enum-as-string).",
    )
    note: Optional[str] = Field(
        default=None,
        max_length=REMINDER_NOTE_MAX_LENGTH,
        description=(
            "The user's own text, shown in the notification. Never holds secrets, "
            "and stripped by NOTIFY_EMAIL_CONTENT_MODE 'minimal' like a card title."
        ),
    )
    status: ReminderStatus = Field(
        default=ReminderStatus.pending,
        sa_type=String,
        description="Lifecycle state (enum-as-string).",
    )
    last_fired_at: Optional[datetime.datetime] = Field(
        default=None,
        description="Naive UTC time it last fired; null until it first does.",
    )
    fire_count: int = Field(
        default=0,
        description=(
            "How often it has fired. Bumped on every fire, and the reason a "
            "monthly reminder can find its original day of month again."
        ),
    )
    anchor_day: Optional[int] = Field(
        default=None,
        description=(
            "Day of month of the *first* occurrence, in the row's own zone, for "
            "monthly recurrence only. Kept because a February fire must not "
            "rewrite a reminder set for the 31st into one for the 28th: the clamp "
            "applies to each computed occurrence, never to the stored intent."
        ),
    )
