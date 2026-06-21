"""In-app notification model (Phase 9 of card sharing).

A ``Notification`` is a persistent, per-user feed entry: "a card was shared with
you", "you were invited to a card", "your public link was opened". It is created
at the existing share events (see ``checkcheckserver.db.notification.emit_notification``,
the single internal seam a later email/transport sub-project can hook into) and
surfaced via ``routes_notification.py``.

Email/SMTP delivery is intentionally **out of scope** for this phase — only the
in-app feed plus a live SSE nudge (``upd_prop="notification"``) is built here.
"""

import datetime
import enum
import uuid
from typing import Optional

from sqlmodel import Field, String, Column, JSON

from checkcheckserver.model._base_model import TimestampedModel

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


log = get_logger()
config = Config()


class NotificationType(str, enum.Enum):
    """What a notification is about. Stored as a string (enum-as-string like
    ``SharePermission`` / ``ShareStatus``)."""

    card_shared = "card_shared"  # a card was instantly shared with the user
    card_invited = "card_invited"  # the user was invited to a card (invite flow)
    public_link_opened = "public_link_opened"  # a public link was opened (to owner)


class NotificationCreate(TimestampedModel, table=False):
    user_id: uuid.UUID = Field(
        foreign_key="user.id",
        ondelete="CASCADE",
        index=True,
        description="The recipient of this notification.",
    )
    type: NotificationType = Field(
        sa_type=String,
        description="What the notification is about (enum-as-string).",
    )
    cl_id: Optional[uuid.UUID] = Field(
        default=None,
        description="The checklist this notification refers to, if any.",
    )
    payload: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSON),
        description=(
            "Optional free-form context for rendering (e.g. actor name/id, card "
            "name). Never holds secrets (no tokens/passphrases)."
        ),
    )
    read_at: Optional[datetime.datetime] = Field(
        default=None,
        description="Naive UTC time the user marked it read; null = unread.",
    )


class Notification(NotificationCreate, table=True):
    __tablename__ = "notification"
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
        unique=True,
    )
