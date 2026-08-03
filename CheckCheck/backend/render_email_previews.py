#!/usr/bin/env python
"""Write one rendered .html per outgoing message kind, for eyeballing without
sending anything (chunk E7, docs/plans/EMAIL_NOTIFICATIONS.md section 7 point 10).

No network and no database: this calls the same rendering functions the
dispatcher and the API use (notify/render.py, notify/invitation.py, the
unsubscribe page), against made-up notifications. Picks up whatever the
environment currently configures (EMAIL_BRAND_COLOR, EMAIL_LOGO_URL,
EMAIL_TEMPLATE_DIR, APP_NAME, ...), so it also doubles as a quick way to check
an operator override before pointing a real instance at it.

Usage (from CheckCheck/backend, with the backend venv active):
    python render_email_previews.py [output_dir]

Defaults to ./email_preview, which is gitignored.
"""

from __future__ import annotations

import datetime
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from checkcheckserver.config import Config
from checkcheckserver.model.checklist_collaborator import SharePermission
from checkcheckserver.notify import invitation, render


UNSUBSCRIBE_URL = "https://example.com/api/notifications/unsubscribe?token=preview"


def _context(
    type: str,
    *,
    actor: str = "Alice",
    card: str = "Groceries",
    note: str | None = None,
    digest: str | None = None,
) -> dict:
    return render.notification_context(
        type=type,
        notification_id=uuid.uuid4(),
        cl_id=uuid.uuid4(),
        payload={"actor_display_name": actor, "checklist_name": card, "note": note},
        created_at=datetime.datetime(2026, 8, 3, 9, 0),
        digest=digest,
    )


def _write(out_dir: Path, name: str, html_body: str) -> None:
    path = out_dir / f"{name}.html"
    path.write_text(html_body)
    print(f"wrote {path}")


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "email_preview"
    out_dir.mkdir(parents=True, exist_ok=True)
    config = Config()

    _write(
        out_dir,
        "single-card_shared",
        render.render_email(
            [_context("card_shared")],
            to="preview@example.com",
            recipient_name="Preview User",
            unsubscribe_url=UNSUBSCRIBE_URL,
            config=config,
        )["html_body"],
    )
    _write(
        out_dir,
        "single-card_invited",
        render.render_email(
            [_context("card_invited")],
            to="preview@example.com",
            recipient_name="Preview User",
            unsubscribe_url=UNSUBSCRIBE_URL,
            config=config,
        )["html_body"],
    )
    _write(
        out_dir,
        "single-public_link_opened",
        render.render_email(
            [_context("public_link_opened")],
            to="preview@example.com",
            recipient_name="Preview User",
            unsubscribe_url=UNSUBSCRIBE_URL,
            config=config,
        )["html_body"],
    )
    _write(
        out_dir,
        "single-reminder_due",
        render.render_email(
            [_context("reminder_due", note="Call the plumber about the boiler")],
            to="preview@example.com",
            recipient_name="Preview User",
            unsubscribe_url=UNSUBSCRIBE_URL,
            config=config,
        )["html_body"],
    )
    _write(
        out_dir,
        "coalesced-card_invited",
        render.render_email(
            [_context("card_invited", card=f"Card {i}") for i in range(3)],
            to="preview@example.com",
            recipient_name="Preview User",
            unsubscribe_url=UNSUBSCRIBE_URL,
            config=config,
        )["html_body"],
    )
    _write(
        out_dir,
        "digest-daily",
        render.render_email(
            [
                _context("card_shared", card=f"Card {i}", digest="daily")
                for i in range(4)
            ],
            to="preview@example.com",
            recipient_name="Preview User",
            unsubscribe_url=UNSUBSCRIBE_URL,
            config=config,
        )["html_body"],
    )
    _write(
        out_dir,
        "invitation",
        invitation.render_public_link_invitation(
            to="stranger@example.com",
            token="preview-token",
            permission=SharePermission.edit,
            checklist_name="Groceries",
            sender_display_name="Alice",
            personal_message="Here is the list we talked about.",
            password_protected=True,
            config=config,
        )["html_body"],
    )

    # Deferred: this router builds its own module-level Config(), and importing
    # it pulls in the whole FastAPI route graph.
    from checkcheckserver.api.routes.routes_notification_settings import (
        _test_email_payload,
        _unsubscribe_page,
    )

    user = SimpleNamespace(
        display_name="Preview User", user_name="preview", email="preview@example.com"
    )
    _write(out_dir, "test_email", _test_email_payload(user)["html_body"])

    confirm = _unsubscribe_page(
        "Stop these emails?",
        "You will no longer receive email about cards being shared with you.",
        "Everything else, including the notifications inside the app, stays as it is.",
        form_action="/api/notifications/unsubscribe?token=preview",
        form_label="Yes, stop these emails",
    )
    _write(out_dir, "unsubscribe_confirm", confirm.body.decode())

    done = _unsubscribe_page(
        "Done",
        "You will no longer receive email about cards being shared with you.",
        "You can turn it back on any time in your notification settings.",
    )
    _write(out_dir, "unsubscribe_done", done.body.decode())

    written = sorted(out_dir.glob("*.html"))
    print(f"\n{len(written)} files in {out_dir}")


if __name__ == "__main__":
    main()
