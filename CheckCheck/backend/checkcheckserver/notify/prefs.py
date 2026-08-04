"""Which notifications reach which channel: the preference resolver (chunk E3).

Nothing here sends anything. This module answers one question, and chunk E4's
fan-out in ``emit_notification`` asks it once per (recipient, type, channel):

    "Given this user's saved preferences and this instance's configuration,
     what should happen with a ``card_shared`` notification on the email
     channel?"

**Order of precedence**, highest first (section 3.1 of the plan):

1. **The instance hard-cap.** A channel whose master switch is off
   (``EMAIL_ENABLED``, ``NOTIFY_WEBHOOK_ENABLED``, ``NOTIFY_PUSH_ENABLED``) and a type listed in
   ``NOTIFY_DISABLED_TYPES`` are ``off`` for everybody, and the API reports them
   as locked so the settings UI can say *your administrator disabled this*.
2. **The user's own choice**, from ``UserNotificationSettings.prefs``.
3. **The instance default**, ``NOTIFY_DEFAULT_MODES``.
4. **The code default** below, for a notification type nobody has configured.

Steps 2 and 3 read operator- and user-written data, so a value that is not a
valid mode for that channel is skipped rather than raised on: an unusable entry
in a YAML file must not break every notification on the instance. The write path
(:func:`validate_prefs_patch`, called by the ``PUT`` endpoint) is where bad input
is rejected loudly.

**Why a missing key is not the same as ``off``**: it is what makes a notification
type added in code work for every existing user without a migration and without
backfilling a single row. A user's stored ``prefs`` only ever holds the entries
they actually decided about.
"""

from __future__ import annotations

import enum
import zoneinfo
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model.notification import NotificationType
from checkcheckserver.model.user_notification_settings import UserNotificationSettings


log = get_logger()


class NotificationMode(str, enum.Enum):
    """How much of a channel a user wants for one notification type.

    ``off``
        Never deliver this type on this channel.
    ``immediate``
        Deliver as it happens (for email, after the suppress window, so reading
        it in the app first cancels the mail).
    ``hourly`` / ``daily``
        Collect into a digest. Email only, and assembled in chunk E4.
    """

    off = "off"
    immediate = "immediate"
    hourly = "hourly"
    daily = "daily"


class PreferenceChannel(str, enum.Enum):
    """Where a notification can go.

    Wider than ``NotificationOutbox.channel``, deliberately: ``in_app`` is not a
    delivery channel (the bell reads the notification table directly, no outbox
    row involved), but it belongs in the same matrix so a user can mute the bell
    for a type in the same place they mute its mail.
    """

    in_app = "in_app"
    email = "email"
    webhook = "webhook"
    push = "push"


# Which modes each channel can actually honour. A digest only means something
# where messages queue up and are assembled later, which is the email channel;
# the bell and a webhook are either live or off.
CHANNEL_MODES: Dict[PreferenceChannel, Tuple[NotificationMode, ...]] = {
    PreferenceChannel.in_app: (NotificationMode.off, NotificationMode.immediate),
    PreferenceChannel.email: (
        NotificationMode.off,
        NotificationMode.immediate,
        NotificationMode.hourly,
        NotificationMode.daily,
    ),
    PreferenceChannel.webhook: (NotificationMode.off, NotificationMode.immediate),
    PreferenceChannel.push: (NotificationMode.off, NotificationMode.immediate),
}

# Where one notification type accepts less than its channel does. Narrowing only:
# an entry here is always a subset of CHANNEL_MODES, because the channel decides
# what is possible and this decides what makes sense.
#
# ``reminder_due`` on email is the one case (plan decision 8). A digest is a
# deliberate delay of up to a day, and a reminder that arrives the morning after
# the thing it was reminding you about is not a reminder, it is a receipt. The
# user picked a time; the only meaningful choices are honouring it or not.
TYPE_CHANNEL_MODES: Dict[Tuple[str, PreferenceChannel], Tuple[NotificationMode, ...]] = {
    (NotificationType.reminder_due.value, PreferenceChannel.email): (
        NotificationMode.off,
        NotificationMode.immediate,
    ),
}

# Shown next to a mode the type does not accept, so the settings dialog and the
# 400 body say the same thing. Keyed like TYPE_CHANNEL_MODES.
TYPE_CHANNEL_MODE_REASON: Dict[Tuple[str, PreferenceChannel], str] = {
    (NotificationType.reminder_due.value, PreferenceChannel.email): (
        "A reminder is sent when it is due, so it cannot go into a digest."
    ),
}

# Step 4 of the precedence: what a notification type nobody configured does.
# The bell stays on, because an in-app notification is the behaviour that already
# exists and costs the user nothing. Mail and webhooks stay off, because a new
# type must never start writing to somebody's inbox on its own: adding it to
# NOTIFY_DEFAULT_MODES is the operator's deliberate act.
CODE_DEFAULT_MODES: Dict[PreferenceChannel, NotificationMode] = {
    PreferenceChannel.in_app: NotificationMode.immediate,
    PreferenceChannel.email: NotificationMode.off,
    PreferenceChannel.webhook: NotificationMode.off,
    # Also off by default like email/webhook, but close to moot either way: a
    # push row is only ever enqueued for a user who already has at least one
    # push_subscription, which is itself an opt-in act (plan section 3.2).
    PreferenceChannel.push: NotificationMode.off,
}

# Longest IANA name is around 32 characters; the column holds 64.
MAX_TIMEZONE_LENGTH = 64


class PreferenceError(ValueError):
    """Base class for a rejected preference write."""


class UnknownPreferenceError(PreferenceError):
    """The request named a type, channel, mode or time zone that does not exist."""


class LockedPreferenceError(PreferenceError):
    """The request tried to change something the administrator has capped."""


def known_types() -> List[str]:
    """Every notification type the code knows about, in declaration order.

    Read from the enum at call time rather than frozen into a table, which is the
    other half of "a new type needs no migration".
    """
    return [member.value for member in NotificationType]


def known_channels() -> List[str]:
    return [member.value for member in PreferenceChannel]


def modes_for(
    channel: PreferenceChannel, type: Optional[str] = None
) -> Tuple[NotificationMode, ...]:
    """What this channel accepts, narrowed to *type* when one is given.

    ``type`` is optional so a caller that only wants to know what a channel can
    do at all (the digest question) does not have to invent a type to ask.
    """
    if type is not None:
        narrowed = TYPE_CHANNEL_MODES.get((type, channel))
        if narrowed is not None:
            return narrowed
    return CHANNEL_MODES[channel]


def allowed_modes(channel: PreferenceChannel, type: Optional[str] = None) -> List[str]:
    return [mode.value for mode in modes_for(channel, type)]


def mode_restriction_reason(
    type: str, channel: PreferenceChannel
) -> Optional[str]:
    """Why this type offers fewer modes than its channel, or None if it does not.

    User-facing, like :func:`lock_reason`, and distinct from it: a lock is
    something an administrator did and could undo, this is a property of the
    notification type that no configuration changes.
    """
    return TYPE_CHANNEL_MODE_REASON.get((type, channel))


def channel_enabled(channel: PreferenceChannel, config: Config) -> bool:
    """Whether the instance can deliver on *channel* at all.

    ``in_app`` has no master switch: the feed is the base feature.
    """
    if channel == PreferenceChannel.email:
        return bool(config.EMAIL_ENABLED)
    if channel == PreferenceChannel.webhook:
        return bool(config.NOTIFY_WEBHOOK_ENABLED)
    if channel == PreferenceChannel.push:
        return bool(config.NOTIFY_PUSH_ENABLED)
    return True


def lock_reason(
    type: str, channel: PreferenceChannel, config: Config
) -> Optional[str]:
    """Why this entry is not the user's to decide, or None if it is.

    The string is user-facing: the settings dialog shows it next to the disabled
    control, so it says what an administrator did without naming a config key.
    """
    if not channel_enabled(channel, config):
        if channel == PreferenceChannel.email:
            return "This server does not send email."
        if channel == PreferenceChannel.push:
            return "This server does not send push notifications."
        return "This server does not send webhooks."
    if type in (config.NOTIFY_DISABLED_TYPES or []):
        return "An administrator disabled this kind of notification on this server."
    return None


def is_locked(type: str, channel: PreferenceChannel, config: Config) -> bool:
    return lock_reason(type, channel, config) is not None


def resolve_mode(
    settings: Optional[UserNotificationSettings],
    type: str,
    channel: PreferenceChannel | str,
    *,
    config: Optional[Config] = None,
) -> NotificationMode:
    """The effective mode for one (user, type, channel), per the precedence above.

    *settings* is the user's row, or ``None`` for a user who never saved any
    preference. Taking the settings row rather than a ``User`` is a small
    deviation from the plan's sketch: the ``User`` row carries no preferences, so
    the caller has to load this one anyway, and passing it in keeps the resolver
    free of database access (chunk E4 loads it once per recipient and asks about
    every channel).
    """
    config = config or Config()
    channel = PreferenceChannel(channel)

    if lock_reason(type, channel, config) is not None:
        return NotificationMode.off

    user_choice = _mode_or_none(
        (settings.prefs or {}).get(type, {}).get(channel.value) if settings else None,
        channel,
        type,
        source="user preference",
    )
    if user_choice is not None:
        return user_choice

    instance_default = _mode_or_none(
        (config.NOTIFY_DEFAULT_MODES or {}).get(type, {}).get(channel.value),
        channel,
        type,
        source="NOTIFY_DEFAULT_MODES",
    )
    if instance_default is not None:
        return instance_default

    return CODE_DEFAULT_MODES[channel]


def _mode_or_none(
    raw, channel: PreferenceChannel, type: str, *, source: str
) -> Optional[NotificationMode]:
    """Parse one stored value, treating anything unusable as "not set".

    Both sources are hand-editable (a YAML file, and a row written by an older
    version of this code), so a value this channel cannot honour must fall
    through to the next precedence level instead of taking the instance down.
    The per-type narrowing is applied here as well as at the write path, because
    a restriction added in a later release finds rows that were legal when they
    were saved: a user who chose a daily digest for a type that later stopped
    offering one inherits the default again rather than keeping a mode nothing
    would honour.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        log.warning("[notify] ignoring non-string mode %r from %s", raw, source)
        return None
    try:
        mode = NotificationMode(raw)
    except ValueError:
        log.warning("[notify] ignoring unknown mode '%s' from %s", raw, source)
        return None
    if mode not in modes_for(channel, type):
        log.warning(
            "[notify] ignoring mode '%s' from %s: '%s' does not support it on the "
            "%s channel",
            raw,
            source,
            type,
            channel.value,
        )
        return None
    return mode


def user_choice(
    settings: Optional[UserNotificationSettings],
    type: str,
    channel: PreferenceChannel,
) -> Optional[str]:
    """What the user explicitly chose here, or None when the entry is inherited.

    The settings dialog needs the difference: "inheriting the instance default"
    and "deliberately set to the same value as the default" look identical in the
    resolved matrix but behave differently when an administrator changes the
    default later.
    """
    if settings is None:
        return None
    value = (settings.prefs or {}).get(type, {}).get(channel.value)
    return value if isinstance(value, str) else None


def resolve_matrix(
    settings: Optional[UserNotificationSettings],
    *,
    config: Optional[Config] = None,
) -> Dict[str, Dict[str, NotificationMode]]:
    """The effective mode for every known type and channel. Used by ``GET``."""
    config = config or Config()
    return {
        type: {
            channel.value: resolve_mode(settings, type, channel, config=config)
            for channel in PreferenceChannel
        }
        for type in known_types()
    }


# ── the write path ────────────────────────────────────────────────────────────


def validate_prefs_patch(
    patch: Dict[str, Dict[str, Optional[str]]],
    *,
    config: Config,
) -> None:
    """Reject a preference patch the server will not store, with a usable message.

    Raises :class:`UnknownPreferenceError` for a type, channel or mode that does
    not exist (or that this channel cannot honour), and
    :class:`LockedPreferenceError` for an entry the administrator has capped. The
    endpoint maps those to 400 and 409.

    A ``None`` value is always allowed: it means "forget my choice here and go
    back to the instance default", which stays legal even for a locked entry,
    since it only ever removes a stored override.
    """
    types = known_types()
    for type, channels in patch.items():
        if type not in types:
            raise UnknownPreferenceError(
                f"Unknown notification type '{type}'. Known types: {', '.join(types)}."
            )
        if not isinstance(channels, dict):
            raise UnknownPreferenceError(
                f"Preferences for '{type}' must be an object of channel to mode."
            )
        for channel_name, mode in channels.items():
            try:
                channel = PreferenceChannel(channel_name)
            except ValueError:
                raise UnknownPreferenceError(
                    f"Unknown channel '{channel_name}'. Known channels: "
                    f"{', '.join(known_channels())}."
                )
            if mode is None:
                continue
            if mode not in allowed_modes(channel, type):
                reason = mode_restriction_reason(type, channel)
                # Two different rejections wearing the same status code: the
                # channel cannot do it at all, or this type does not offer it.
                # Only the second one has something to explain.
                detail = (
                    f"Mode '{mode}' is not valid for '{type}' on the "
                    f"{channel.value} channel. {reason}"
                    if reason
                    else f"Mode '{mode}' is not valid for the {channel.value} channel."
                )
                raise UnknownPreferenceError(
                    f"{detail} Valid modes: {', '.join(allowed_modes(channel, type))}."
                )
            reason = lock_reason(type, channel, config)
            if reason is not None:
                raise LockedPreferenceError(reason)


def apply_prefs_patch(
    current: Optional[dict],
    patch: Dict[str, Dict[str, Optional[str]]],
) -> dict:
    """Merge *patch* into *current* and return a **new** dict.

    Partial by design: a type the patch does not mention keeps its stored
    entries, and a channel it does not mention keeps its stored mode. A ``None``
    value deletes the entry, which is how a user goes back to inheriting the
    instance default. An empty per-type object is dropped, so a row does not
    accumulate rubbish.

    A new dict rather than an in-place edit because SQLAlchemy does not track
    mutation inside a JSON column: the attribute has to be *assigned* for the
    change to be written.
    """
    merged = {
        type: dict(channels) for type, channels in (current or {}).items() if channels
    }
    for type, channels in patch.items():
        entry = merged.get(type, {})
        for channel_name, mode in channels.items():
            if mode is None:
                entry.pop(channel_name, None)
            else:
                entry[channel_name] = mode
        if entry:
            merged[type] = entry
        else:
            merged.pop(type, None)
    return merged


@lru_cache(maxsize=1)
def _iana_timezone_names() -> frozenset:
    """Every zone name this machine's IANA database knows.

    Cached: the lookup walks the whole zoneinfo tree, and the answer cannot
    change while the process runs. Empty (rather than an exception) on a system
    with no zone database at all, in which case validation falls back to asking
    ``ZoneInfo`` directly.
    """
    try:
        return frozenset(zoneinfo.available_timezones())
    except Exception:  # pragma: no cover - only on a system without tzdata
        log.warning("[notify] no IANA time zone database found on this system")
        return frozenset()


def validate_timezone(name: str) -> str:
    """Return *name* if it is a real IANA zone, else raise.

    Checked against the zone database rather than a regular expression, because
    the only thing that matters is whether ``ZoneInfo`` can later resolve it.
    """
    if not isinstance(name, str) or not name.strip():
        raise UnknownPreferenceError("A time zone must be a non-empty name.")
    name = name.strip()
    if len(name) > MAX_TIMEZONE_LENGTH:
        raise UnknownPreferenceError("That time zone name is too long to be real.")
    known = _iana_timezone_names()
    if known and name not in known:
        raise UnknownPreferenceError(
            f"Unknown time zone '{name}'. Use an IANA name such as 'Europe/Berlin'."
        )
    try:
        zoneinfo.ZoneInfo(name)
    except Exception:
        raise UnknownPreferenceError(
            f"Unknown time zone '{name}'. Use an IANA name such as 'Europe/Berlin'."
        )
    return name


def validate_webhook_url(url: str) -> str:
    """Basic shape check for a user-supplied webhook target.

    Deliberately shallow: this only keeps obvious nonsense out of the column.
    The part that matters, refusing URLs that resolve into the server's own
    network, is the SSRF guard in chunk E6, at the moment the request is made,
    because a hostname's address can change between here and there.
    """
    if not isinstance(url, str) or not url.strip():
        raise UnknownPreferenceError("A webhook URL must be a non-empty string.")
    url = url.strip()
    if len(url) > 2048:
        raise UnknownPreferenceError("That webhook URL is too long.")
    if not url.startswith(("http://", "https://")):
        raise UnknownPreferenceError("A webhook URL must start with http:// or https://.")
    return url
