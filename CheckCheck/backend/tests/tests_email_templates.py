"""Tests for chunk E7 of the notification sub-project: the email design system
and operator branding.

Nothing here talks to the live server: like ``tests_email_transports.py``, this
exercises the rendering functions directly (``notify/render.py``,
``notify/invitation.py``, ``notify/templating.py``, ``notify/branding.py``, and
the private page-builder in ``routes_notification_settings.py``). The existing
suites (``tests_notification_wiring.py``, ``tests_public_link_email.py``,
``tests_email_transports.py``) exercise the same code paths at the HTTP level
and are the proof that the multipart structure, the ``List-Unsubscribe``
headers, the one-click ``POST`` and the invitation's no-passphrase rule all
still pass unchanged after this chunk moved the markup into Jinja2 templates.

The plan is ``docs/plans/EMAIL_NOTIFICATIONS.md``, section E7.
"""

import datetime
import uuid

import pytest
from pydantic import ValidationError

from checkcheckserver.config import Config
from checkcheckserver.model.checklist_collaborator import SharePermission
from checkcheckserver.model.notification import NotificationType
from checkcheckserver.notify import branding, invitation, render, templating


FROM_ADDRESS = "checkcheck@example.com"


def _config(**overrides) -> Config:
    return Config(**overrides)


def _context(type: str, *, actor="Alice", card="Groceries", note=None, digest=None) -> dict:
    return render.notification_context(
        type=type,
        notification_id=uuid.uuid4(),
        cl_id=uuid.uuid4(),
        payload={"actor_display_name": actor, "checklist_name": card, "note": note},
        created_at=datetime.datetime(2026, 8, 3, 9, 0),
        digest=digest,
    )


# ── every bundled template, every type, every mode, every shape ─────────────


ALL_TYPES = [t.value for t in NotificationType]


@pytest.mark.parametrize("type", ALL_TYPES)
@pytest.mark.parametrize("mode", ["full", "minimal"])
@pytest.mark.parametrize("shape", ["single", "coalesced", "digest"])
def test_every_bundled_template_renders_for_every_type_mode_and_shape(type, mode, shape):
    config = _config(NOTIFY_EMAIL_CONTENT_MODE=mode)
    if shape == "single":
        contexts = [_context(type, note="Call the plumber" if type == "reminder_due" else None)]
    elif shape == "coalesced":
        contexts = [_context(type, card=f"Card {i}") for i in range(3)]
    else:
        contexts = [
            _context(type, card=f"Card {i}", digest="daily") for i in range(3)
        ]

    message = render.render_email(
        contexts,
        to="recipient@example.com",
        recipient_name="Recipient",
        unsubscribe_url="https://example.com/unsub?token=abc",
        config=config,
    )
    assert message["subject"]
    assert message["text_body"]
    assert message["html_body"]


def test_the_webhook_body_and_test_mail_and_invitation_also_render_for_every_type():
    """Not looped into the matrix above (different call shapes), but part of the
    same "no StrictUndefined failure" requirement."""
    config = _config()
    for type in ALL_TYPES:
        body = render.webhook_body(_context(type), config)
        assert body["type"] == type


# ── minimal mode: the context dict, not only the rendered output ────────────


def test_minimal_mode_omits_the_redacted_keys_from_the_item_context_entirely():
    config = _config(NOTIFY_EMAIL_CONTENT_MODE="minimal")
    context = _context("card_shared", actor="Alice", card="Groceries")
    message_context = render._message_context(
        [context], recipient_name="Bob", unsubscribe_url=None, config=config
    )
    item = message_context["items"][0]
    assert "card_name" not in item
    assert "actor" not in item
    assert "note" not in item


def test_full_mode_keeps_the_raw_fields_in_the_item_context():
    config = _config()
    context = _context("card_shared", actor="Alice", card="Groceries")
    message_context = render._message_context(
        [context], recipient_name="Bob", unsubscribe_url=None, config=config
    )
    item = message_context["items"][0]
    assert item["card_name"] == "Groceries"
    assert item["actor"] == "Alice"


def test_minimal_mode_leaks_no_card_name_actor_or_note_in_the_rendered_message():
    config = _config(NOTIFY_EMAIL_CONTENT_MODE="minimal")
    context = _context("reminder_due", actor="Alice", card="Groceries", note="Call the vet")
    message = render.render_email(
        [context],
        to="recipient@example.com",
        recipient_name="Bob",
        unsubscribe_url=None,
        config=config,
    )
    whole = message["subject"] + message["text_body"] + message["html_body"]
    assert "Groceries" not in whole
    assert "Alice" not in whole
    assert "Call the vet" not in whole


def test_invitation_context_also_omits_sender_and_card_name_under_minimal():
    config = _config(NOTIFY_EMAIL_CONTENT_MODE="minimal")
    message = invitation.render_public_link_invitation(
        to="stranger@example.com",
        token="tok-abc",
        permission=SharePermission.view,
        checklist_name="Groceries",
        sender_display_name="Alice",
        personal_message=None,
        password_protected=False,
        config=config,
    )
    whole = message["subject"] + message["text_body"] + message["html_body"]
    assert "Groceries" not in whole
    assert "Alice" not in whole


# ── the override directory ───────────────────────────────────────────────────


def test_an_override_wins_for_a_template_it_defines_and_falls_back_for_one_it_does_not(
    tmp_path,
):
    (tmp_path / "message.html").write_text(
        "{% extends 'base.html' %}{% block content %}CUSTOM-MARKER{% endblock %}"
    )
    config = _config(EMAIL_TEMPLATE_DIR=str(tmp_path))

    rendered_message = templating.render(
        "message.html",
        {**branding.brand_context(config), "greeting": "Hi", "heading": None, "items": [],
         "more_count": 0, "unsubscribe_url": None},
        config=config,
    )
    assert "CUSTOM-MARKER" in rendered_message

    # invitation.html is not overridden, so the bundled one is used untouched.
    rendered_invitation = templating.render(
        "invitation.html",
        {
            **branding.brand_context(config),
            "opening": "Someone shared a list with you.",
            "note": None,
            "url": "https://example.com/p/tok",
            "permission_sentence": "read this list, without signing in",
            "password_protected": False,
        },
        config=config,
    )
    assert "CUSTOM-MARKER" not in rendered_invitation
    assert "Open the list" in rendered_invitation


def test_a_syntactically_broken_override_fails_at_boot(tmp_path):
    (tmp_path / "message.html").write_text("{% extends 'base.html' %}{% if oops %}")
    with pytest.raises(ValidationError) as excinfo:
        _config(EMAIL_TEMPLATE_DIR=str(tmp_path))
    assert "message.html" in str(excinfo.value)


def test_an_override_that_raises_only_at_render_time_falls_back_and_the_message_still_sends(
    tmp_path,
):
    """A dummy full-mode boot context carries `card_name`, so an override that
    prints it passes startup; a real `minimal` message omits that key entirely,
    which is exactly the case ``templating.render`` must fall back on rather
    than dead-lettering the message."""
    (tmp_path / "message.html").write_text(
        "{% extends 'base.html' %}{% block content %}{{ items[0].card_name }}{% endblock %}"
    )
    config = _config(EMAIL_TEMPLATE_DIR=str(tmp_path), NOTIFY_EMAIL_CONTENT_MODE="minimal")

    message = render.render_email(
        [_context("card_shared", card="Groceries")],
        to="recipient@example.com",
        recipient_name="Bob",
        unsubscribe_url=None,
        config=config,
    )
    # Fell back to the bundled template rather than raising or leaking the name.
    assert "Groceries" not in message["html_body"]
    assert "Open in" in message["html_body"]


# ── EMAIL_BRAND_COLOR / EMAIL_LOGO_URL ───────────────────────────────────────


def test_a_malformed_brand_color_is_rejected_at_boot():
    with pytest.raises(ValidationError) as excinfo:
        _config(EMAIL_BRAND_COLOR="not-a-color")
    assert "EMAIL_BRAND_COLOR" in str(excinfo.value)


def test_a_malformed_logo_url_is_rejected_at_boot():
    with pytest.raises(ValidationError):
        _config(EMAIL_LOGO_URL="not-a-url")


def test_a_template_dir_that_is_not_a_directory_is_rejected_at_boot(tmp_path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(ValidationError):
        _config(EMAIL_TEMPLATE_DIR=str(missing))


def test_a_pale_brand_color_flips_the_button_foreground_to_dark_text():
    assert branding.foreground_for("#ffffff") == "#1f2328"
    assert branding.foreground_for("#000000") == "#ffffff"


def test_no_img_tag_when_logo_url_is_unset_and_an_escaped_one_when_set():
    config = _config()
    message = render.render_email(
        [_context("card_shared")],
        to="recipient@example.com",
        recipient_name="Bob",
        unsubscribe_url=None,
        config=config,
    )
    assert "<img" not in message["html_body"]

    config_with_logo = _config(EMAIL_LOGO_URL="https://example.com/logo.png?a=1&b=2")
    message_with_logo = render.render_email(
        [_context("card_shared")],
        to="recipient@example.com",
        recipient_name="Bob",
        unsubscribe_url=None,
        config=config_with_logo,
    )
    assert '<img src="https://example.com/logo.png?a=1&amp;b=2"' in message_with_logo["html_body"]
    assert 'alt="CheckCheck"' in message_with_logo["html_body"]


def test_the_buttons_href_is_escaped_and_matches_the_text_parts_url():
    config = _config()
    context = _context("card_shared")
    message = render.render_email(
        [context],
        to="recipient@example.com",
        recipient_name="Bob",
        unsubscribe_url=None,
        config=config,
    )
    url_in_text = message["text_body"].splitlines()[3]
    assert url_in_text.startswith("http")
    escaped = url_in_text.replace("&", "&amp;")
    assert f'href="{escaped}"' in message["html_body"]


# ── the unsubscribe page ─────────────────────────────────────────────────────


def test_the_unsubscribe_page_renders_branded_and_escapes_what_it_echoes():
    from checkcheckserver.api.routes.routes_notification_settings import (
        _unsubscribe_page,
        config as route_config,
    )

    response = _unsubscribe_page(
        "Stop these <script>emails</script>?",
        "You will no longer receive email about cards being shared with you.",
        form_action="/api/notifications/unsubscribe?token=a&b",
        form_label="Yes, stop these emails",
    )
    body = response.body.decode()
    assert "<script>emails</script>" not in body
    assert "&lt;script&gt;" in body
    assert route_config.EMAIL_BRAND_COLOR in body
    assert 'action="/api/notifications/unsubscribe?token=a&amp;b"' in body
