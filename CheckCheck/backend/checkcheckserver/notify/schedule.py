"""When a queued message becomes due, and which messages share a fate (chunk E4).

Three of the plan's behaviours (section 4.1) are really one mechanism seen from
different angles, and they all live here as scheduling decisions taken at enqueue
time:

**Suppression.** An ``immediate`` message is not due immediately: it waits
``NOTIFY_EMAIL_SUPPRESS_WINDOW_SECONDS``. If the recipient reads the notification
in the app inside that window, the dispatcher cancels the mail instead of sending
it (``outbox._deliver_claimed``). Somebody who is looking at the app right now
does not need mail about what they are looking at.

**Coalescing.** Messages that share a ``dedupe_key`` and come due together are
delivered as one message. For an immediate share that key is the recipient, the
type and the actor, which is what turns "Anna shared 30 cards with you" into one
mail instead of thirty.

**Digests.** An ``hourly`` or ``daily`` message is scheduled to the end of its
window and keyed on that window, so it uses the very same coalescing path: every
notification landing in one window carries the same key and the same due time,
and the dispatcher finds them together. Nothing separate assembles a digest, and
an empty window costs nothing because no rows exist for it.
"""

from __future__ import annotations

import datetime
import uuid
import zoneinfo
from typing import Optional

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.notify.prefs import NotificationMode


log = get_logger()


# The local hour a daily digest goes out. Morning rather than midnight, so the
# summary arrives when somebody might read it, and so "yesterday's notifications"
# is what the message actually contains.
DAILY_DIGEST_HOUR = 8


def _zone(timezone_name: Optional[str]) -> datetime.tzinfo:
    """The recipient's zone, falling back to UTC.

    A stored zone can stop being valid (a renamed IANA zone, an image without a
    zone database), and a digest arriving at the wrong hour is a far better
    outcome than a notification that raises.
    """
    if not timezone_name:
        return datetime.timezone.utc
    try:
        return zoneinfo.ZoneInfo(timezone_name)
    except Exception:
        log.warning("[notify] unusable time zone '%s', using UTC", timezone_name)
        return datetime.timezone.utc


def immediate_due_at(now: datetime.datetime, config: Config) -> datetime.datetime:
    """When an ``immediate`` message may go out: after the suppression window."""
    window = max(int(config.NOTIFY_EMAIL_SUPPRESS_WINDOW_SECONDS or 0), 0)
    return now + datetime.timedelta(seconds=window)


def digest_due_at(
    mode: NotificationMode,
    *,
    now: datetime.datetime,
    timezone_name: Optional[str] = None,
) -> datetime.datetime:
    """The end of the digest window *now* falls into, as naive UTC.

    Hourly windows are aligned to the clock hour in UTC, daily ones to
    :data:`DAILY_DIGEST_HOUR` in the recipient's own zone. Two notifications in
    the same window always get the same answer here, which is what makes the
    dedupe key below identical for them.
    """
    if mode == NotificationMode.hourly:
        start = now.replace(minute=0, second=0, microsecond=0)
        return start + datetime.timedelta(hours=1)

    zone = _zone(timezone_name)
    local = now.replace(tzinfo=datetime.timezone.utc).astimezone(zone)
    target = local.replace(
        hour=DAILY_DIGEST_HOUR, minute=0, second=0, microsecond=0
    )
    if target <= local:
        target = target + datetime.timedelta(days=1)
    return target.astimezone(datetime.timezone.utc).replace(tzinfo=None)


def immediate_dedupe_key(
    *, user_id: uuid.UUID, type: str, actor_id: Optional[str]
) -> str:
    """What an immediate message coalesces with: same recipient, type and actor.

    Deliberately not keyed on the card: the whole point is to collapse *many
    cards* from one person into one message. A notification with no actor (a
    public link was opened, where the actor is an anonymous visitor) keys on the
    type alone, which is right too: several links opened at once are one mail.
    """
    return f"{user_id}:{type}:{actor_id or '-'}"


def digest_dedupe_key(
    *, user_id: uuid.UUID, mode: NotificationMode, due_at: datetime.datetime
) -> str:
    """What a digest message coalesces with: everything in the same window.

    Across notification types on purpose, since a digest is one message about
    everything that happened, and keyed on the window's end so a later
    notification in the same window joins the message already queued for it.
    """
    return f"{user_id}:digest:{mode.value}:{due_at.isoformat()}"
