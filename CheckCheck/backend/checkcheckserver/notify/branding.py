"""Per-type accent colours and the operator's own brand colour (chunk E7).

Two different palettes, deliberately kept apart:

* **The per-type accent** (:data:`_TYPE_ACCENT`) is a module constant, not
  config. It has to stay legible against the message's own background, and it
  is what makes a full inbox scannable by event kind at a glance. It discloses
  nothing extra under ``NOTIFY_EMAIL_CONTENT_MODE: minimal``, since the subject
  line there already names the kind of event.
* **``EMAIL_BRAND_COLOR``** is the operator's own identity, used for the header
  and the call-to-action buttons. :func:`foreground_for` picks black or white
  against it so a pale brand colour cannot produce white text on a white
  button.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from checkcheckserver.config import Config


HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def validate_hex_color(value: str) -> str:
    """Return *value* unchanged, or raise if it is not a ``#rrggbb`` triplet."""
    if not HEX_COLOR_RE.match(value):
        raise ValueError(f"{value!r} is not a hex color in the form #rrggbb.")
    return value


def foreground_for(hex_color: str) -> str:
    """Black or white, whichever reads better on *hex_color*.

    Relative luminance (ITU-R BT.601 weights), the same rough formula browsers
    use for their own fallback contrast decisions. Not colour-science exact,
    but enough to keep a pale brand colour from producing white text on a
    white button.
    """
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "#1f2328" if luminance > 150 else "#ffffff"


# glyph, colour. Fixed and not configurable: see the module docstring.
_TYPE_ACCENT: dict = {
    "card_shared": ("\U0001F465", "#2563eb"),  # busts in silhouette, blue
    "card_invited": ("✉", "#7c3aed"),  # envelope, violet
    "public_link_opened": ("\U0001F517", "#0891b2"),  # link, teal
    "reminder_due": ("⏰", "#d97706"),  # alarm clock, amber
}
# For a notification type this build has never heard of.
_DEFAULT_ACCENT: Tuple[str, str] = ("•", "#6b7280")  # bullet, grey


def accent_for(type: Optional[str]) -> Tuple[str, str]:
    """``(glyph, colour)`` for *type*, or a neutral default for an unknown one."""
    return _TYPE_ACCENT.get(type or "", _DEFAULT_ACCENT)


def brand_context(config: Config) -> dict:
    """The header/footer variables every template shares."""
    return {
        "app_name": config.APP_NAME,
        "brand_color": config.EMAIL_BRAND_COLOR,
        "brand_fg": foreground_for(config.EMAIL_BRAND_COLOR),
        "logo_url": config.EMAIL_LOGO_URL,
        "public_url": (config.SERVER_PUBLIC_URL or "").rstrip("/") + "/",
    }
