"""Dummy contexts for :func:`templating.check_all_templates` (chunk E7).

Deliberately shaped like a **full**-mode message, optional keys (``card_name``,
``actor``, ``note``) included: those keys only exist in the real context when
``NOTIFY_EMAIL_CONTENT_MODE`` is ``full`` (see ``render._item_context``), and a
bundled template never references them, so including them here does not change
whether the bundled templates pass. What it does buy is a boot check that lets
an override referencing ``{{ item.card_name }}`` pass startup and still catches
it the moment a real ``minimal`` message needs rendering and that key is gone,
which is exactly the failure the render-time fallback in ``templating.render``
exists for.
"""

from __future__ import annotations

from checkcheckserver.config import Config
from checkcheckserver.notify import branding


def dummy_context(name: str, config: Config) -> dict:
    context = branding.brand_context(config)
    glyph, color = branding.accent_for("card_shared")

    if name in ("message.html", "message.txt"):
        context.update(
            {
                "greeting": "Hello,",
                "heading": None,
                "items": [
                    {
                        "type": "card_shared",
                        "line": "Someone shared a card with you.",
                        "url": context["public_url"],
                        "glyph": glyph,
                        "color": color,
                        "card_name": "Example card",
                        "actor": "Someone",
                        "note": None,
                    }
                ],
                "more_count": 0,
                "unsubscribe_url": context["public_url"],
            }
        )
    elif name in ("invitation.html", "invitation.txt"):
        context.update(
            {
                "opening": "Someone shared a list with you.",
                "sender": "Someone",
                "card_name": "Example card",
                "note": None,
                "url": context["public_url"],
                "permission_sentence": "read this list, without signing in",
                "password_protected": False,
            }
        )
    elif name in ("test_email.html", "test_email.txt"):
        context.update({"greeting": "Someone"})
    elif name == "unsubscribe_page.html":
        context.update(
            {
                "title": "Stop these emails?",
                "lead": "You will no longer receive email about cards being shared with you.",
                "detail": "Everything else stays as it is.",
                "form_action": context["public_url"],
                "form_label": "Yes, stop these emails",
            }
        )
    return context
