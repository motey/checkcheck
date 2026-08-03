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

from typing import Optional
from urllib.parse import quote

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model.checklist_collaborator import SharePermission
from checkcheckserver.notify import branding, templating
from checkcheckserver.notify.transports import subject_line


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
    """The invitation's subject, sanitised as a whole (:func:`subject_line`).

    Same reason as ``render._subject_for_one``: the card name and the sender's
    display name are free text, and a newline in either one would make the
    message unsendable rather than merely ugly.
    """
    return subject_line(_wording(sender, card, config))


def _wording(sender: Optional[str], card: Optional[str], config: Config) -> str:
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
    sender = _sender_name(sender_display_name, config)
    card = _card_name(checklist_name, config)
    url = public_link_url(token, config)
    note = (personal_message or "").strip() or None
    if note and len(note) > MAX_PERSONAL_MESSAGE_LENGTH:
        # Callers validate first and get a 400; truncating here is the belt to
        # that braces, so an oversized note can never reach a mail server.
        note = note[:MAX_PERSONAL_MESSAGE_LENGTH]

    context = branding.brand_context(config)
    context.update(
        {
            "opening": _opening(sender, card, context["app_name"]),
            "note": note,
            "url": url,
            "permission_sentence": permission_sentence(permission),
            "password_protected": password_protected,
        }
    )
    if config.NOTIFY_EMAIL_CONTENT_MODE != "minimal":
        # Escape hatch for an override that wants the raw fields rather than
        # the precomputed `opening` sentence. Absent entirely under `minimal`,
        # same reasoning as render.py's `_item_context`.
        context["sender"] = sender
        context["card_name"] = card

    return {
        "to": to,
        "subject": _subject(sender, card, config),
        "text_body": templating.render("invitation.txt", context, config=config),
        "html_body": templating.render("invitation.html", context, config=config),
        # No List-Unsubscribe: there is no subscription and no account to switch
        # anything off for. Auto-Submitted is added by the transport.
        "headers": {},
    }


def _opening(sender: Optional[str], card: Optional[str], app_name: str) -> str:
    who = sender or "Someone"
    what = f'the list "{card}"' if card else "a list"
    return f"{who} shared {what} with you on {app_name}."
