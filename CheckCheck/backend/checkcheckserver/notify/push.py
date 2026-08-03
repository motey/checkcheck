"""The push channel: Web Push to a subscribed browser or installed PWA (chunk
P1 of ``docs/plans/SYSTEM_NOTIFICATIONS.md``).

Mirrors ``notify/webhooks.py``'s shape (transient/permanent errors, a
``Protocol`` sender with a capturing test double), with one structural
difference from every other channel: **the target is not fixed at enqueue
time.** An email or webhook row carries the one address it goes to; a push row
carries only ``(user_id, notification_id)``, and delivering it means looping
over every ``push_subscription`` row that user currently has and sending to
each (``notify/outbox.py``'s ``_deliver_push``). A user can have several
devices subscribed, and a device can come and go independently of any one
notification.

**Per-subscription outcome, not per-row.** ``pywebpush`` raising a 404 or 410
means that *specific* subscription is gone (the browser unregistered it, the
user cleared site data, the push service expired it): that one
``push_subscription`` row is deleted and delivery moves on to the next one, it
never fails the outbox row by itself. A 429 or 5xx is the push service asking
to be tried again later, exactly like a webhook receiver's 5xx.

**Note on the module import order.** This module is imported from inside
``Config``'s own boot-time validator (``_validate_push``), so, like
``notify/templating.py``, it must never import ``checkcheckserver.log``
(which does ``Config()`` at import time and would recurse into a second,
partially-initialised ``Config`` construction). Plain ``logging.getLogger``
instead.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from checkcheckserver.config import Config


log = logging.getLogger("CheckCheck")


# Short, mirroring the webhook timeout: a push service that needs longer than
# this is retried, not waited on, and must not hold up the queue behind it.
PUSH_TIMEOUT_SECONDS = 10

# Byte lengths of the two pieces of a VAPID key pair: an uncompressed P-256
# point (0x04 followed by 32-byte X and Y) and a raw 32-byte scalar.
_VAPID_PUBLIC_KEY_BYTES = 65
_VAPID_PRIVATE_KEY_BYTES = 32


# ── errors ────────────────────────────────────────────────────────────────────


class PushDeliveryError(Exception):
    """Base class for a failed attempt to push to one subscription."""


class TransientPushError(PushDeliveryError):
    """The push service may accept a later attempt. Retry the whole row."""


class PermanentPushError(PushDeliveryError):
    """This one subscription will never accept another message.

    Scoped to the subscription, not the row: the caller deletes the
    ``push_subscription`` row this came from and keeps going, it does not by
    itself fail the delivery to a recipient's other devices.
    """


# ── the delivery ──────────────────────────────────────────────────────────────


@dataclass
class PushSubscriptionTarget:
    """One device to push to, as much as the sender needs to reach it."""

    id: uuid.UUID
    endpoint: str
    p256dh: str
    auth: str


@dataclass
class OutgoingPush:
    """One notification, before it is encrypted for any particular device."""

    title: str
    body: str
    url: str
    tag: str


# ── the sender ────────────────────────────────────────────────────────────────


@runtime_checkable
class PushSender(Protocol):
    """How a push message physically leaves the server.

    Mirrors ``WebhookSender``/``EmailTransport``: raises a transient or a
    permanent error and returns None on success, leaving the retry decision
    to the outbox.
    """

    name: str

    async def send(
        self, push: OutgoingPush, target: PushSubscriptionTarget, config: Config
    ) -> None: ...


class PywebpushSender:
    """Sends through ``pywebpush``, off the event loop.

    ``pywebpush`` is built on the synchronous ``requests`` library, so the
    actual call runs in a thread (``asyncio.to_thread``), the same reasoning
    as every other blocking library this codebase calls from async code.
    """

    name = "pywebpush"

    async def send(
        self, push: OutgoingPush, target: PushSubscriptionTarget, config: Config
    ) -> None:
        await asyncio.to_thread(_send_sync, push, target, config)


def _send_sync(
    push: OutgoingPush, target: PushSubscriptionTarget, config: Config
) -> None:
    from pywebpush import WebPushException, webpush

    data = json.dumps(
        {"title": push.title, "body": push.body, "url": push.url, "tag": push.tag}
    )
    private_key = config.VAPID_PRIVATE_KEY.get_secret_value() if config.VAPID_PRIVATE_KEY else None
    try:
        webpush(
            subscription_info={
                "endpoint": target.endpoint,
                "keys": {"p256dh": target.p256dh, "auth": target.auth},
            },
            data=data,
            vapid_private_key=private_key,
            vapid_claims={"sub": f"mailto:{config.VAPID_CONTACT_EMAIL}"},
            ttl=max(int(config.NOTIFY_PUSH_TTL_SECONDS or 0), 0),
            timeout=PUSH_TIMEOUT_SECONDS,
        )
    except WebPushException as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code in (404, 410):
            # The browser unregistered it, the user cleared site data, or the
            # push service itself expired it. This exact subscription is done.
            raise PermanentPushError(
                f"The push service reports this subscription is gone ({status_code})."
            ) from exc
        if status_code == 429 or (status_code is not None and status_code >= 500):
            raise TransientPushError(
                f"The push service answered {status_code}."
            ) from exc
        if status_code is not None:
            raise PermanentPushError(
                f"The push service answered {status_code}. {exc.message}".strip()
            ) from exc
        # No response at all (network error, timeout): worth another attempt.
        raise TransientPushError(str(exc)) from exc
    except Exception as exc:  # a network-level failure from requests itself
        raise TransientPushError(
            f"Could not deliver the push message: {exc.__class__.__name__}: {exc}"
        ) from exc


# ── selection (test seam, like the webhook sender) ─────────────────────────────

_default_sender = PywebpushSender()
_override: Optional[PushSender] = None


def get_push_sender() -> PushSender:
    """The sender the application should use, honouring a test override."""
    return _override if _override is not None else _default_sender


def set_push_sender(sender: Optional[PushSender]) -> None:
    global _override
    _override = sender


def reset_push_sender() -> None:
    set_push_sender(None)


class CapturingPushSender:
    """Keeps every push in memory instead of sending it. Tests only."""

    name = "capturing"

    def __init__(self):
        self.sent: list = []
        # Keyed by subscription id, so a test can make exactly one target fail
        # while the others succeed.
        self.raise_for: dict = {}
        self.raise_on_send: Optional[Exception] = None

    async def send(
        self, push: OutgoingPush, target: PushSubscriptionTarget, config: Config
    ) -> None:
        if target.id in self.raise_for:
            raise self.raise_for[target.id]
        if self.raise_on_send is not None:
            raise self.raise_on_send
        self.sent.append((push, target))

    def clear(self) -> None:
        self.sent.clear()
        self.raise_for.clear()


# ── VAPID key validation (config boot check) ────────────────────────────────


def validate_vapid_keys(*, public_key: str, private_key: str) -> None:
    """Raise :class:`ValueError` when these are not a usable VAPID key pair.

    Called from ``Config``'s own boot validator: a malformed key would
    otherwise only be discovered the first time a push message is queued, in
    a background task nobody is watching. Checks the shape (base64url, the
    right byte length for an uncompressed P-256 point / a raw 32-byte scalar)
    and then asks ``py_vapid`` to actually load the private key, which is
    what ``notify/push.py`` does on every send.
    """
    public_bytes = _b64url_decode(public_key, "VAPID_PUBLIC_KEY")
    if len(public_bytes) != _VAPID_PUBLIC_KEY_BYTES or public_bytes[:1] != b"\x04":
        raise ValueError(
            "VAPID_PUBLIC_KEY must be the base64url-encoded uncompressed P-256 point "
            f"({_VAPID_PUBLIC_KEY_BYTES} bytes, starting with 0x04)."
        )
    private_bytes = _b64url_decode(private_key, "VAPID_PRIVATE_KEY")
    if len(private_bytes) != _VAPID_PRIVATE_KEY_BYTES:
        raise ValueError(
            "VAPID_PRIVATE_KEY must be a base64url-encoded "
            f"{_VAPID_PRIVATE_KEY_BYTES}-byte P-256 scalar."
        )

    from py_vapid import Vapid02

    try:
        Vapid02.from_string(private_key)
    except Exception as exc:
        raise ValueError(f"could not load the private key: {exc}") from exc


def _b64url_decode(value: str, name: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except Exception as exc:
        raise ValueError(f"{name} is not valid base64url: {exc}") from exc
