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
``minimal`` neither a card name nor a person's name appears anywhere in the
message, including the subject and the HTML part.

**Links come from ``SERVER_PUBLIC_URL`` only**, never from request headers, and
always in the shape ``/?card={cl_id}&n={notification_id}``. The ``n`` parameter
is what lets the SPA mark the notification read once the card is actually on
screen (chunk E5): marking it in a redirect endpoint would let a mail scanner
that pre-fetches links mark everything read before the human ever looked.
"""

from __future__ import annotations

import datetime
import html
import uuid
from typing import Dict, List, Optional
from urllib.parse import quote

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.notify.transports import message_id_domain


log = get_logger()


# Key under which the render context lives inside an outbox payload. Its
# presence is also what marks a row as coalescable: the test message and, later,
# the public-link invitation are complete messages with no notification behind
# them, and must never be merged into somebody's digest.
CONTEXT_KEY = "notify"

# An upper bound on how many notifications one message lists. Beyond this the
# message says "and N more" and the reader opens the app instead.
MAX_LISTED = 20


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


def _subject_for_one(context: dict, config: Config) -> str:
    type = context.get("type")
    actor = _actor_name(context, config)
    card = _card_name(context, config)
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


def _text_body(
    contexts: List[dict],
    *,
    recipient_name: Optional[str],
    unsubscribe_url: Optional[str],
    config: Config,
) -> str:
    app_name = config.APP_NAME
    listed = contexts[:MAX_LISTED]
    lines = [_greeting(recipient_name), ""]
    for context in listed:
        lines.append(_line_for(context, config))
        lines.append(card_url(context, config))
        lines.append("")
    if len(contexts) > len(listed):
        lines.append(f"...and {len(contexts) - len(listed)} more.")
        lines.append("")
    lines.append("-- ")
    lines.append(f"You are receiving this because of your notification settings in {app_name}.")
    lines.append(f"Change them here: {(config.SERVER_PUBLIC_URL or '').rstrip('/')}/")
    if unsubscribe_url:
        lines.append(f"Stop receiving mail like this: {unsubscribe_url}")
    return "\n".join(lines) + "\n"


def _html_body(
    contexts: List[dict],
    *,
    recipient_name: Optional[str],
    unsubscribe_url: Optional[str],
    config: Config,
) -> str:
    """The HTML alternative.

    Inline styles and a table-free layout on purpose: mail clients strip
    stylesheets, and this message has to stay readable in all of them. Every
    piece of user-controlled text (a display name, a card title) is escaped,
    even though the recipient is entitled to see it: it is still text somebody
    else typed.
    """
    app_name = html.escape(config.APP_NAME)
    public_url = (config.SERVER_PUBLIC_URL or "").rstrip("/") + "/"
    listed = contexts[:MAX_LISTED]
    parts = [
        '<div style="font-family:system-ui,-apple-system,Segoe UI,Helvetica,Arial,'
        'sans-serif;font-size:15px;line-height:1.5;color:#1f2328">',
        f"<p>{html.escape(_greeting(recipient_name))}</p>",
    ]
    for context in listed:
        url = html.escape(card_url(context, config), quote=True)
        parts.append(
            f"<p>{html.escape(_line_for(context, config))}<br>"
            f'<a href="{url}">Open it in {app_name}</a></p>'
        )
    if len(contexts) > len(listed):
        parts.append(f"<p>...and {len(contexts) - len(listed)} more.</p>")
    parts.append('<hr style="border:none;border-top:1px solid #d8dee4;margin:24px 0">')
    footer = (
        f"You are receiving this because of your notification settings in "
        f'<a href="{html.escape(public_url, quote=True)}">{app_name}</a>.'
    )
    if unsubscribe_url:
        escaped = html.escape(unsubscribe_url, quote=True)
        footer += f' <a href="{escaped}">Stop receiving mail like this</a>.'
    parts.append(f'<p style="font-size:13px;color:#59636e">{footer}</p>')
    parts.append("</div>")
    return "".join(parts)
