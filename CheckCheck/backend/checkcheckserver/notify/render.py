"""What a notification looks like as an email (chunk E4).

Every message is rendered **twice**: once at enqueue time, from the notification
that just happened, and possibly a second time at delivery time, when several
queued messages for the same recipient collapse into one (see
:func:`render_group_payload` and the coalescing in ``notify/outbox.py``). Both
paths go through :func:`render_email`, so a coalesced message cannot drift away
from a single one.

**The context, not the notification.** A message is rendered from a small
snapshot dict (:func:`notification_context`) that is stored in the outbox row's
payload, never from a live read of the card. By the time a delayed message is
sent, the card may have been renamed, unshared or deleted, and re-reading it
would either leak state the recipient is no longer allowed to see or crash on a
missing row. The snapshot is taken when the access check has just passed.

**Two content modes.** ``NOTIFY_EMAIL_CONTENT_MODE`` decides how much leaves the
instance. ``full`` names the card and the person who acted, which is what makes
the message useful in an inbox. ``minimal`` says only that something happened
and links back to the app, for instances where a card title is sensitive. In
``minimal`` neither a card name, nor a person's name, nor the note on a reminder
appears anywhere in the message, including the subject and the HTML part.

**Links come from ``SERVER_PUBLIC_URL`` only**, never from request headers, and
always in the shape ``/?card={cl_id}&n={notification_id}``. The ``n`` parameter
is what lets the SPA mark the notification read once the card is actually on
screen (chunk E5): marking it in a redirect endpoint would let a mail scanner
that pre-fetches links mark everything read before the human ever looked.
"""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Dict, List, Optional
from urllib.parse import quote

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.notify import branding, templating
from checkcheckserver.notify.transports import message_id_domain, subject_line


log = get_logger()


# Key under which the render context lives inside an outbox payload. Its
# presence is also what marks a row as coalescable: the test message and, later,
# the public-link invitation are complete messages with no notification behind
# them, and must never be merged into somebody's digest.
CONTEXT_KEY = "notify"

# An upper bound on how many notifications one message lists. Beyond this the
# message says "and N more" and the reader opens the app instead.
MAX_LISTED = 20

# How large a push payload may be, in bytes of the JSON that ``notify/push.py``
# hands to ``pywebpush``. The practical Web Push limit is around 4 KB *after*
# encryption, and every push service enforces its own version of it by answering
# 413, so the plaintext budget sits well under that with room for the encryption
# overhead. A module constant rather than a setting (decision 2 of
# ``docs/plans/NOTIFICATIONS_REWORK.md``): it is a property of the protocol, not
# of a deployment, and an operator has nothing to gain by tuning it.
PUSH_PAYLOAD_MAX_BYTES = 3072

# How much of that budget the body keeps when the title would otherwise eat all
# of it. A body is shortened before a title is, but never to nothing: the outbox
# rejects a payload with an empty body, so truncation must not be able to turn a
# deliverable notification into an unqueueable one.
PUSH_BODY_RESERVE_BYTES = 256


def notification_context(
    *,
    type: str,
    notification_id: Optional[uuid.UUID],
    cl_id: Optional[uuid.UUID],
    payload: Optional[dict],
    created_at: datetime.datetime,
    digest: Optional[str] = None,
) -> dict:
    """The snapshot one notification is rendered from, later and possibly twice.

    Only the keys the templates use are copied out of the notification payload,
    so a producer that starts putting more context in the feed payload does not
    silently start mailing it out.
    """
    payload = payload or {}
    actor = payload.get("actor_display_name") or payload.get("actor_user_name")
    return {
        "type": type,
        "notification_id": str(notification_id) if notification_id else None,
        "cl_id": str(cl_id) if cl_id else None,
        "actor": actor or None,
        "checklist_name": payload.get("checklist_name") or None,
        # The user's own text on a reminder (chunk R2). Null for every other
        # type, and stripped in `minimal` exactly like a card title: it is text
        # somebody typed about a card, and it leaves the instance the same way.
        "note": payload.get("note") or None,
        "created_at": created_at.isoformat(),
        "digest": digest,
    }


def card_url(context: dict, config: Config) -> str:
    """Deep link to the card this notification is about.

    ``/?card=…&n=…`` rather than a dedicated route, because the client is a
    single page: the query parameters are read by ``pages/index.vue``, which
    opens the card overlay and then marks the notification read.
    """
    base = (config.SERVER_PUBLIC_URL or "").rstrip("/")
    cl_id = context.get("cl_id")
    if not cl_id:
        return base or "/"
    url = f"{base}/?card={quote(str(cl_id))}"
    notification_id = context.get("notification_id")
    if notification_id:
        url += f"&n={quote(str(notification_id))}"
    return url


# ── wording ───────────────────────────────────────────────────────────────────


def _minimal(config: Config) -> bool:
    return config.NOTIFY_EMAIL_CONTENT_MODE == "minimal"


def _card_name(context: dict, config: Config) -> Optional[str]:
    """The card's name, or None when it must not be told (or is unknown)."""
    if _minimal(config):
        return None
    return context.get("checklist_name")


def _actor_name(context: dict, config: Config) -> Optional[str]:
    if _minimal(config):
        return None
    return context.get("actor")


def _note(context: dict, config: Config) -> Optional[str]:
    """The reminder's own text, or None when it must not be told.

    Held to the same rule as a card title even though the recipient wrote it
    themselves: ``minimal`` is the operator's answer to how much may leave the
    instance, and a note is exactly the kind of thing ("call the clinic about
    the results") that made them pick it.
    """
    if _minimal(config):
        return None
    return context.get("note")


def _subject_for_one(context: dict, config: Config) -> str:
    """The subject of a message about one notification, ready for the header.

    The wording is built first and sanitised as a whole (:func:`subject_line`),
    because the card name and the reminder note interpolated into it are free
    text and a newline in either one would otherwise dead-letter the message.
    """
    return subject_line(_wording_for_one(context, config))


def _wording_for_one(context: dict, config: Config) -> str:
    type = context.get("type")
    actor = _actor_name(context, config)
    card = _card_name(context, config)
    if type == "reminder_due":
        # The note first when there is one: it is what the user wrote to their
        # future self, and it is the only part of the subject they chose.
        note = _note(context, config)
        if note:
            return f"Reminder: {note}"
        if card:
            return f'Reminder: "{card}"'
        return "Reminder"
    if type == "card_invited":
        if actor and card:
            return f'{actor} invited you to "{card}"'
        if card:
            return f'You were invited to "{card}"'
        return "You were invited to a card"
    if type == "public_link_opened":
        if card:
            return f'Your public link to "{card}" was opened'
        return "One of your public links was opened"
    # card_shared, and anything a later release adds without its own wording.
    if actor and card:
        return f'{actor} shared "{card}" with you'
    if card:
        return f'"{card}" was shared with you'
    return "A card was shared with you"


def _subject_for_many(contexts: List[dict], config: Config) -> str:
    """The subject of a coalesced message or a digest. Sanitised like the
    single-notification one: an actor's display name reaches this one too."""
    return subject_line(_wording_for_many(contexts, config))


def _wording_for_many(contexts: List[dict], config: Config) -> str:
    count = len(contexts)
    digest = contexts[0].get("digest")
    if digest in ("hourly", "daily"):
        period = "hourly" if digest == "hourly" else "daily"
        return f"Your {period} summary: {count} notifications"

    types = {context.get("type") for context in contexts}
    actors = {_actor_name(context, config) for context in contexts}
    actor = actors.pop() if len(actors) == 1 else None
    if len(types) > 1:
        return f"{count} new notifications"
    type = types.pop()
    if type == "reminder_due":
        # No note in the plural subject: several reminders have several notes,
        # and picking one of them to stand for all would be a lie.
        return f"{count} reminders"
    if type == "card_invited":
        if actor:
            return f"{actor} invited you to {count} cards"
        return f"You were invited to {count} cards"
    if type == "public_link_opened":
        return f"{count} of your public links were opened"
    if actor:
        return f"{actor} shared {count} cards with you"
    return f"{count} cards were shared with you"


def _line_for(context: dict, config: Config) -> str:
    """One notification as a single sentence of plain text."""
    type = context.get("type")
    actor = _actor_name(context, config)
    card = _card_name(context, config)
    subject_card = f'the card "{card}"' if card else "a card"
    if type == "reminder_due":
        note = _note(context, config)
        if note:
            return f"Reminder about {subject_card}: {note}"
        return f"You asked to be reminded about {subject_card}."
    if type == "card_invited":
        who = actor or "Someone"
        return f"{who} invited you to {subject_card}."
    if type == "public_link_opened":
        return f"Your public link to {subject_card} was opened for the first time."
    who = actor or "Someone"
    return f"{who} shared {subject_card} with you."


# ── the message ───────────────────────────────────────────────────────────────


def render_email(
    contexts: List[dict],
    *,
    to: str,
    recipient_name: Optional[str],
    unsubscribe_url: Optional[str],
    config: Config,
) -> dict:
    """Render one message covering *contexts*, as an outbox payload.

    The returned dict is exactly what ``outbox.enqueue`` stores and what
    ``outbox`` hands back to the transport: ``to``, ``subject``, ``text_body``,
    ``html_body``, ``headers``, plus the contexts themselves under
    :data:`CONTEXT_KEY` so the message can be re-rendered together with others
    that came due at the same time.
    """
    if not contexts:
        raise ValueError("Cannot render a notification email without a context.")

    single = contexts[0] if len(contexts) == 1 else None
    subject = (
        _subject_for_one(single, config)
        if single is not None
        else _subject_for_many(contexts, config)
    )
    return {
        "to": to,
        # Kept so a message re-rendered at delivery time can greet the recipient
        # the same way the single messages would have.
        "recipient_name": recipient_name,
        "subject": subject,
        "text_body": _text_body(
            contexts,
            recipient_name=recipient_name,
            unsubscribe_url=unsubscribe_url,
            config=config,
        ),
        "html_body": _html_body(
            contexts,
            recipient_name=recipient_name,
            unsubscribe_url=unsubscribe_url,
            config=config,
        ),
        "headers": _headers(contexts, unsubscribe_url=unsubscribe_url, config=config),
        CONTEXT_KEY: contexts,
    }


def render_group_payload(payloads: List[dict], config: Config) -> dict:
    """Merge several queued messages for one recipient into a single one.

    Called by the dispatcher when rows sharing a ``dedupe_key`` come due
    together. Everything about the recipient (the address, the unsubscribe link)
    is taken from the oldest row, since all of them belong to the same user and
    the same notification type or digest window.
    """
    contexts: List[dict] = []
    for payload in payloads:
        contexts.extend(payload.get(CONTEXT_KEY) or [])
    if not contexts:
        raise ValueError("None of these queued messages carries a render context.")
    first = payloads[0]
    return render_email(
        contexts,
        to=first["to"],
        recipient_name=first.get("recipient_name"),
        unsubscribe_url=_unsubscribe_url_of(first),
        config=config,
    )


def webhook_body(context: dict, config: Config) -> dict:
    """The JSON one notification becomes on the webhook channel (chunk E6).

    Flat and boring on purpose: a receiver is somebody's script, so the shape has
    to be readable without this file, and every key is always present (null
    rather than absent) so a consumer can index it without guarding.

    ``NOTIFY_EMAIL_CONTENT_MODE`` is honoured here too, despite its name. It is
    the operator's answer to "how much may leave this instance", and a webhook
    leaves it just as thoroughly as an email does; an instance set to ``minimal``
    would be surprised to find card titles in an outbound POST body.
    """
    return {
        "type": context.get("type"),
        "notification_id": context.get("notification_id"),
        "checklist_id": context.get("cl_id"),
        "checklist_name": _card_name(context, config),
        "actor": _actor_name(context, config),
        # Null for everything except a reminder, and null there too under
        # `minimal`. Present either way, like every other key here.
        "note": _note(context, config),
        "created_at": context.get("created_at"),
        "url": card_url(context, config),
        "app": config.APP_NAME,
        # The wording the same notification uses in an inbox, so a receiver that
        # just wants to print something has it without a lookup table.
        "text": _line_for(context, config),
    }


def _minimal_push(config: Config) -> bool:
    """Whether push notifications are in `minimal` mode.

    Deliberately its own flag rather than :func:`_minimal`: decision 3 of the
    system-notifications plan is that ``NOTIFY_PUSH_CONTENT_MODE`` is
    independent of ``NOTIFY_EMAIL_CONTENT_MODE``, since a push notification
    sits on a lock screen, a more exposed surface than an email behind an
    inbox app's own unlock.
    """
    return config.NOTIFY_PUSH_CONTENT_MODE == "minimal"


def _push_title(context: dict, config: Config) -> str:
    """One short line naming what happened, for a push notification's title.

    Its own wording rather than a reuse of :func:`_subject_for_one`: that one
    is keyed to the email content mode, and a lock-screen notification has far
    less room than an inbox subject line.
    """
    minimal = _minimal_push(config)
    type = context.get("type")
    actor = None if minimal else context.get("actor")
    card = None if minimal else context.get("checklist_name")

    if type == "reminder_due":
        if minimal:
            return "Reminder"
        note = context.get("note")
        if note:
            return f"Reminder: {note}"
        if card:
            return f'Reminder: "{card}"'
        return "Reminder"
    if type == "card_invited":
        if minimal:
            return "You were invited to a card"
        if actor and card:
            return f'{actor} invited you to "{card}"'
        if card:
            return f'You were invited to "{card}"'
        return "You were invited to a card"
    if type == "public_link_opened":
        if minimal:
            return "One of your public links was opened"
        if card:
            return f'Your public link to "{card}" was opened'
        return "One of your public links was opened"
    # card_shared, and anything a later release adds without its own wording.
    if minimal:
        return "A card was shared with you"
    if actor and card:
        return f'{actor} shared "{card}" with you'
    if card:
        return f'"{card}" was shared with you'
    return "A card was shared with you"


def _push_body(context: dict, config: Config) -> str:
    """The one-line body under the title. Empty in `minimal` mode: the title
    already says as much as `minimal` allows, and a body would only repeat it
    or, worse, invite padding it out with exactly the details `minimal` is
    supposed to withhold."""
    if _minimal_push(config):
        return config.APP_NAME
    type = context.get("type")
    if type == "reminder_due":
        card = context.get("checklist_name")
        return f'About "{card}"' if card else "Tap to open the card."
    return config.APP_NAME


def push_payload(context: dict, config: Config, *, tag: str) -> dict:
    """The ``{title, body, url, tag}`` an outbox row snapshots for the push
    channel (plan section 3.2).

    Like :func:`webhook_body`, built from the render context rather than
    re-reading the card, and rendered once at enqueue time: by the time a
    push row is due the card may have changed, and the message must reflect
    what was true when the notification happened. ``tag`` is passed in rather
    than derived here because it is the same dedupe key ``notify/schedule.py``
    computes for email coalescing (plan section 3.2), which this module has
    no reason to know how to build.

    Kept inside :data:`PUSH_PAYLOAD_MAX_BYTES`, so a long card name plus a long
    reminder note arrives shortened rather than not at all.
    """
    return _within_push_budget(
        {
            "title": _push_title(context, config),
            "body": _push_body(context, config),
            "url": card_url(context, config),
            "tag": tag,
        }
    )


def _push_payload_size(payload: dict) -> int:
    """How many bytes *payload* is on the wire, measured the way
    ``notify/push.py`` serialises it (``json.dumps`` of the same four keys, in
    UTF-8). Measured rather than estimated because JSON escaping is what makes
    the difference: a quotation mark in a card name costs two bytes and an
    emoji costs twelve."""
    return len(json.dumps(payload).encode("utf-8"))


def _json_cost(value: str) -> int:
    """What *value* adds to the serialised payload: its escaped length in
    bytes, without the quotes around it (those belong to the payload's fixed
    part). Costs are additive, which is what lets the budget be split between
    the title and the body without re-serialising for every candidate."""
    return len(json.dumps(value).encode("utf-8")) - 2


def _within_push_budget(payload: dict) -> dict:
    """*payload*, shortened until it fits :data:`PUSH_PAYLOAD_MAX_BYTES`.

    The body is given up before the title: the title is the line that says what
    happened, so it has first claim on the budget. The body still keeps
    :data:`PUSH_BODY_RESERVE_BYTES` of room, both because a lock-screen
    notification with nothing under its title reads like a bug and because an
    empty body is not a valid push payload at all
    (``notify/outbox.py``'s ``_validate_push_payload``).

    ``url`` and ``tag`` are never touched: a truncated link is a broken link,
    and the tag is the dedupe key the service worker matches on.
    """
    if _push_payload_size(payload) <= PUSH_PAYLOAD_MAX_BYTES:
        return payload

    title, body = payload.get("title") or "", payload.get("body") or ""
    # What is left for the two of them once the url, the tag and the JSON
    # punctuation have taken their share.
    available = PUSH_PAYLOAD_MAX_BYTES - _push_payload_size(
        {**payload, "title": "", "body": ""}
    )
    if available < 2 * PUSH_BODY_RESERVE_BYTES:
        # The url and the tag have taken so much that neither field could keep
        # anything worth reading, which takes a SERVER_PUBLIC_URL of absurd
        # length. Shortening them to nothing would only produce an invalid
        # payload; this goes out as it is, and a push service answering 413
        # fails the outbox row (chunk N3), which at least leaves a trace.
        log.warning(
            "[notify] a push payload's url and tag alone exceed %s bytes",
            PUSH_PAYLOAD_MAX_BYTES,
        )
        return payload

    payload["title"] = _clipped_to(
        title, available - min(_json_cost(body), PUSH_BODY_RESERVE_BYTES)
    )
    payload["body"] = _clipped_to(body, available - _json_cost(payload["title"]))
    log.debug(
        "[notify] push payload shortened to %s bytes", _push_payload_size(payload)
    )
    return payload


def _clipped_to(text: str, allowance: int) -> str:
    """The longest prefix of *text* that costs at most *allowance* bytes, with
    an ellipsis marking what was cut.

    A binary search over the prefix length rather than a character-at-a-time
    loop: the answer is monotone (a longer prefix never serialises shorter) and
    the search costs a dozen measurements instead of a few thousand.
    """

    def clipped(length: int) -> str:
        if length >= len(text):
            return text
        if length <= 0:
            return ""
        return text[:length].rstrip() + "…"

    if _json_cost(text) <= allowance:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _json_cost(clipped(middle)) <= allowance:
            low = middle
        else:
            high = middle - 1
    return clipped(low)


def _unsubscribe_url_of(payload: dict) -> Optional[str]:
    raw = (payload.get("headers") or {}).get("List-Unsubscribe")
    return raw[1:-1] if raw and raw.startswith("<") and raw.endswith(">") else raw


def _headers(
    contexts: List[dict], *, unsubscribe_url: Optional[str], config: Config
) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if unsubscribe_url:
        headers["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        # RFC 8058: tells the mail client it may unsubscribe with a single POST
        # to that URL, without opening a browser and without a confirmation step.
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    cards = {context.get("cl_id") for context in contexts if context.get("cl_id")}
    if len(cards) == 1:
        # Everything about one card threads together in the recipient's client.
        # A message covering several cards deliberately gets no thread: it does
        # not belong under any one of them.
        reference = f"<card-{cards.pop()}@{message_id_domain(config)}>"
        headers["References"] = reference
        headers["In-Reply-To"] = reference
    return headers


def _greeting(recipient_name: Optional[str]) -> str:
    return f"Hello {recipient_name}," if recipient_name else "Hello,"


def _heading(contexts: List[dict]) -> Optional[str]:
    """The digest heading, or None for a single message or a plain coalesced one."""
    if len(contexts) <= 1:
        return None
    digest = contexts[0].get("digest")
    if digest == "hourly":
        return "Your hourly summary"
    if digest == "daily":
        return "Your daily summary"
    return None


def _item_context(context: dict, config: Config) -> dict:
    """One notification as a template-ready dict.

    ``line`` and ``url`` are fully resolved sentences: a bundled template never
    needs anything else. ``card_name``, ``actor`` and ``note`` are included only
    in ``full`` mode, as an escape hatch for an operator override that wants
    the raw fields rather than the precomputed sentence; the keys are simply
    absent under ``minimal``, which is what keeps such an override from
    printing a card title on an instance that said it may not leave (see the
    module docstring and ``templating.render``'s fallback).
    """
    glyph, color = branding.accent_for(context.get("type"))
    item = {
        "type": context.get("type"),
        "line": _line_for(context, config),
        "url": card_url(context, config),
        "glyph": glyph,
        "color": color,
    }
    if not _minimal(config):
        item["card_name"] = context.get("checklist_name")
        item["actor"] = context.get("actor")
        item["note"] = context.get("note")
    return item


def _message_context(
    contexts: List[dict],
    *,
    recipient_name: Optional[str],
    unsubscribe_url: Optional[str],
    config: Config,
) -> dict:
    listed = contexts[:MAX_LISTED]
    context = branding.brand_context(config)
    context.update(
        {
            "greeting": _greeting(recipient_name),
            "heading": _heading(contexts),
            "items": [_item_context(c, config) for c in listed],
            "more_count": max(0, len(contexts) - len(listed)),
            "unsubscribe_url": unsubscribe_url,
        }
    )
    return context


def _text_body(
    contexts: List[dict],
    *,
    recipient_name: Optional[str],
    unsubscribe_url: Optional[str],
    config: Config,
) -> str:
    context = _message_context(
        contexts, recipient_name=recipient_name, unsubscribe_url=unsubscribe_url, config=config
    )
    return templating.render("message.txt", context, config=config)


def _html_body(
    contexts: List[dict],
    *,
    recipient_name: Optional[str],
    unsubscribe_url: Optional[str],
    config: Config,
) -> str:
    context = _message_context(
        contexts, recipient_name=recipient_name, unsubscribe_url=unsubscribe_url, config=config
    )
    return templating.render("message.html", context, config=config)
