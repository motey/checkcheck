"""The one message that is not a notification: mailing a public link (chunk E6).

Everything else in ``notify/`` renders something that happened *to the recipient*
and that they have preferences about. This message is the opposite: somebody with
an account asks the server to send a capability link to an address that may not
belong to any account at all. That changes three things, and they are the reason
this lives in its own module rather than in ``render.py``:

* **No preferences, no unsubscribe.** There is no user behind the address, so
  there is nothing to resolve and no ``List-Unsubscribe`` secret to sign with.
  The message says who sent it and that it was a one-off.
* **No render context**, so the row can never be folded into somebody's digest
  (``outbox._is_coalescable`` keys on exactly that), which is what keeps an
  invitation a complete message on its own.
* **The passphrase is never in it.** A protected link is announced as protected
  and the sender is told to pass the passphrase on out of band. Putting it in the
  same message would make the protection decorative.

``NOTIFY_EMAIL_CONTENT_MODE`` is honoured here as well: on a ``minimal``
instance neither the card's name nor the sender's name leaves the server, and the
message is only the link plus what it grants. Mail goes to an address a user
typed, so a typo sends it to a stranger, which is precisely the case that setting
exists for.
"""

from __future__ import annotations

import html
from typing import Optional
from urllib.parse import quote

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model.checklist_collaborator import SharePermission


log = get_logger()


# How long a personal note may be. Long enough for "hi, this is the shopping list
# we talked about", short enough that the feature is not a way to mail arbitrary
# text to arbitrary people through somebody else's server.
MAX_PERSONAL_MESSAGE_LENGTH = 500


def public_link_url(token: str, config: Config) -> str:
    """The address the recipient opens, always built from ``SERVER_PUBLIC_URL``.

    Never from request headers: a ``Host`` a caller controls would let one user
    mail a link pointing at somebody else's server.
    """
    base = (config.SERVER_PUBLIC_URL or "").rstrip("/")
    return f"{base}/p/{quote(token, safe='')}"


def permission_sentence(permission: SharePermission | str) -> str:
    """What the link grants, as a sentence a non-technical recipient can act on.

    The plain words matter: this is also what the client shows in its confirm
    step, so both sides describe the same thing the same way.
    """
    value = permission.value if isinstance(permission, SharePermission) else str(permission)
    if value == SharePermission.edit.value:
        return "add, change and tick off items on this list, without signing in"
    if value == SharePermission.check.value:
        return "tick items off this list, without signing in"
    return "read this list, without signing in"


def _sender_name(sender_display_name: Optional[str], config: Config) -> Optional[str]:
    if config.NOTIFY_EMAIL_CONTENT_MODE == "minimal":
        return None
    return sender_display_name or None


def _card_name(checklist_name: Optional[str], config: Config) -> Optional[str]:
    if config.NOTIFY_EMAIL_CONTENT_MODE == "minimal":
        return None
    return checklist_name or None


def _subject(sender: Optional[str], card: Optional[str], config: Config) -> str:
    app_name = config.APP_NAME
    if sender and card:
        return f'{sender} shared the list "{card}" with you'
    if sender:
        return f"{sender} shared a list with you"
    if card:
        return f'A list was shared with you: "{card}"'
    return f"A list was shared with you on {app_name}"


def render_public_link_invitation(
    *,
    to: str,
    token: str,
    permission: SharePermission | str,
    checklist_name: Optional[str],
    sender_display_name: Optional[str],
    personal_message: Optional[str],
    password_protected: bool,
    config: Config,
) -> dict:
    """The whole message as an outbox payload, ready for ``outbox.enqueue``.

    Deliberately the same dict shape every other queued email uses (``to``,
    ``subject``, ``text_body``, ``html_body``, ``headers``) so the dispatcher
    needs to know nothing about invitations, minus the render context that would
    make it coalescable.
    """
    app_name = config.APP_NAME
    sender = _sender_name(sender_display_name, config)
    card = _card_name(checklist_name, config)
    url = public_link_url(token, config)
    note = (personal_message or "").strip() or None
    if note and len(note) > MAX_PERSONAL_MESSAGE_LENGTH:
        # Callers validate first and get a 400; truncating here is the belt to
        # that braces, so an oversized note can never reach a mail server.
        note = note[:MAX_PERSONAL_MESSAGE_LENGTH]

    return {
        "to": to,
        "subject": _subject(sender, card, config),
        "text_body": _text_body(
            sender=sender,
            card=card,
            url=url,
            note=note,
            permission=permission,
            password_protected=password_protected,
            app_name=app_name,
        ),
        "html_body": _html_body(
            sender=sender,
            card=card,
            url=url,
            note=note,
            permission=permission,
            password_protected=password_protected,
            app_name=app_name,
        ),
        # No List-Unsubscribe: there is no subscription and no account to switch
        # anything off for. Auto-Submitted is added by the transport.
        "headers": {},
    }


def _opening(sender: Optional[str], card: Optional[str], app_name: str) -> str:
    who = sender or "Someone"
    what = f'the list "{card}"' if card else "a list"
    return f"{who} shared {what} with you on {app_name}."


def _text_body(
    *,
    sender: Optional[str],
    card: Optional[str],
    url: str,
    note: Optional[str],
    permission: SharePermission | str,
    password_protected: bool,
    app_name: str,
) -> str:
    lines = ["Hello,", "", _opening(sender, card, app_name), ""]
    if note:
        # Quoted so a reader can tell the sender's words from the server's, and
        # so a note styled to look like part of the message cannot pass for it.
        for line in note.splitlines():
            lines.append(f"> {line}")
        lines.append("")
    lines.append(f"Open the list here: {url}")
    lines.append("")
    lines.append(f"With this link you can {permission_sentence(permission)}.")
    if password_protected:
        lines.append(
            "The link is protected by a passphrase. Ask the person who sent it "
            "to you: it is deliberately not in this message."
        )
    lines.append("")
    lines.append("-- ")
    lines.append(
        f"You received this because somebody using {app_name} entered your address. "
        "It is a one-off message; you are not subscribed to anything and no account "
        "was created for you. If it was not meant for you, please ignore it and do "
        "not pass the link on."
    )
    return "\n".join(lines) + "\n"


def _html_body(
    *,
    sender: Optional[str],
    card: Optional[str],
    url: str,
    note: Optional[str],
    permission: SharePermission | str,
    password_protected: bool,
    app_name: str,
) -> str:
    """The HTML alternative, with every piece of typed text escaped.

    Two of the values here come straight from a user: the personal note and the
    card's name. The note is the more interesting one, since its author chose the
    recipient too, which is exactly the shape of a phishing attempt: it is escaped
    and rendered as a quotation, and no link inside it is ever made clickable.
    """
    escaped_url = html.escape(url, quote=True)
    escaped_app = html.escape(app_name)
    parts = [
        '<div style="font-family:system-ui,-apple-system,Segoe UI,Helvetica,Arial,'
        'sans-serif;font-size:15px;line-height:1.5;color:#1f2328">',
        "<p>Hello,</p>",
        f"<p>{html.escape(_opening(sender, card, app_name))}</p>",
    ]
    if note:
        parts.append(
            '<blockquote style="margin:16px 0;padding:8px 14px;border-left:3px solid '
            '#d8dee4;color:#59636e;white-space:pre-wrap">'
            f"{html.escape(note)}</blockquote>"
        )
    parts.append(
        f'<p><a href="{escaped_url}" style="display:inline-block;padding:10px 18px;'
        'border-radius:6px;background:#1f2328;color:#fff;text-decoration:none">'
        "Open the list</a></p>"
    )
    parts.append(f'<p style="font-size:13px;word-break:break-all">{escaped_url}</p>')
    parts.append(
        f"<p>With this link you can {html.escape(permission_sentence(permission))}.</p>"
    )
    if password_protected:
        parts.append(
            "<p>The link is protected by a passphrase. Ask the person who sent it to "
            "you: it is deliberately not in this message.</p>"
        )
    parts.append('<hr style="border:none;border-top:1px solid #d8dee4;margin:24px 0">')
    parts.append(
        '<p style="font-size:13px;color:#59636e">'
        f"You received this because somebody using {escaped_app} entered your address. "
        "It is a one-off message; you are not subscribed to anything and no account was "
        "created for you. If it was not meant for you, please ignore it and do not pass "
        "the link on.</p>"
    )
    parts.append("</div>")
    return "".join(parts)
