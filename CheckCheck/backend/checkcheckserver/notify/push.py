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

**Only 404 and 410 delete a subscription** (chunk N3, finding 2). Every other
4xx, and a refusal from the guard below, is a
:class:`PushEndpointRefused`: the message is not going to be delivered and
retrying will not help, but the device is not the thing that is wrong. A 401
from a push service is a broken VAPID key and a 413 is a payload this server
built too large, and answering either by unsubscribing every device on the
instance is the failure mode this classification exists to prevent.

**The endpoint is a target a user chose, so it is guarded** (chunk N2 of
``docs/plans/NOTIFICATIONS_REWORK.md``). It arrives as a string in a request
body, and ``pywebpush`` does not check it either: without a guard, any account
holder could register ``https://127.0.0.1:5432/`` and use this server as a probe
into networks only it can reach. So the endpoint is judged by
``notify/net_guard.py`` twice: once at registration
(``api/routes/routes_notification_settings.py``) and once again here, right
before the send, because a host that resolved publicly at registration can
resolve privately later.

**This is resolve-then-judge without pinning, and that is weaker than the
webhook channel's guard.** ``notify/webhooks.py`` closes the window between the
check and the connection by connecting to the address it judged and refusing
redirects. ``pywebpush`` is built on ``requests`` and offers no hook for either:
it resolves the name a second time itself and follows redirects. So a name that
answers "public address" to the check and "127.0.0.1" a millisecond later to the
connection, and a push service that answers 302 to a private address, are both
still reachable here. Closing that would mean replacing ``pywebpush``'s
transport, which is a larger change than this guard. Do not read the two
channels as equivalent.

**Note on the module import order.** This module is imported from inside
``Config``'s own boot-time validator (``_validate_push``), so, like
``notify/templating.py``, it must never import ``checkcheckserver.log``
(which does ``Config()`` at import time and would recurse into a second,
partially-initialised ``Config`` construction). Plain ``logging.getLogger``
instead, and the same rule holds for ``notify/net_guard.py``, which this module
imports.
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
from checkcheckserver.notify import net_guard


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
    """No later attempt at this message will come out differently.

    Never raised directly: what an attempt has to do about it differs so much
    between the two cases below that the *type* is what carries the
    consequence, and a handler that catches this base class is choosing the
    non-destructive one (chunk N3 of ``docs/plans/NOTIFICATIONS_REWORK.md``,
    finding 2).
    """


class PushSubscriptionGone(PermanentPushError):
    """The push service says this subscription no longer exists (404, 410).

    The one and only reason to delete a ``push_subscription`` row: the browser
    unregistered it, the user cleared site data, or the push service expired
    it. Scoped to the subscription, not the row: the caller deletes that row
    and keeps going, it does not by itself fail the delivery to a recipient's
    other devices.
    """


class PushEndpointRefused(PermanentPushError):
    """This request was refused, and the subscription is not known to be dead.

    Two things arrive here: an endpoint the SSRF guard will not call (chunk
    N2), and a push service answering any 4xx other than 404/410 (chunk N3).
    They are the same event from the row's point of view, "this message is not
    going to be delivered and retrying will not help", and crucially neither
    one is evidence that the *device* is gone:

    * a 401 or 403 is a broken or mismatched VAPID key, a server
      misconfiguration that would otherwise unsubscribe every device on the
      instance one debug line at a time,
    * a 413 is a payload this server built too large,
    * a guard refusal means a host started resolving privately, and deleting on
      it would let a rebinding host quietly unsubscribe somebody's device.

    So ``notify/outbox.py``'s ``_deliver_push`` fails the **outbox row** on
    this (kept as ``failed`` for inspection) and touches no subscription.

    Retrying is pointless for the same reason it is for ``WebhookUrlRefused``:
    a host that resolves into a private range now will almost certainly resolve
    there again, and a rejected VAPID signature stays rejected until an
    operator fixes the configuration.
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

    # Judged again here, not only at registration: a host that resolved publicly
    # when the browser subscribed can resolve privately by the time this row is
    # drained. Already off the event loop (``asyncio.to_thread``), so the
    # blocking resolver is the right one.
    try:
        net_guard.require_public_https_target_sync(
            target.endpoint, what=f"push subscription {target.id}"
        )
    except net_guard.AddressRefused as exc:
        raise PushEndpointRefused(f"This push endpoint is refused: {exc}") from exc
    except net_guard.HostResolutionError as exc:
        raise TransientPushError(
            f"Could not resolve the push endpoint's host: {exc}"
        ) from exc

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
            # push service itself expired it. This exact subscription is done,
            # and this is the only status that says so.
            raise PushSubscriptionGone(
                f"The push service reports this subscription is gone ({status_code})."
            ) from exc
        if status_code == 429 or (status_code is not None and status_code >= 500):
            raise TransientPushError(
                f"The push service answered {status_code}."
            ) from exc
        if status_code is not None:
            # Every other answer (401, 403, 400, 413, ...) is the push service
            # refusing this request, which is almost always this server's fault
            # rather than the device's. The subscription stays; the row fails so
            # an operator can see it (finding 2).
            raise PushEndpointRefused(
                f"The push service refused this request ({status_code}). "
                f"{exc.message}".strip()
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
    right byte length for an uncompressed P-256 point / a raw 32-byte scalar),
    asks ``py_vapid`` to actually load the private key, which is what
    ``notify/push.py`` does on every send, and finally that the two halves
    **belong together** (finding 9).

    That last check is what "a usable key pair" has always claimed to mean. The
    public key is handed to every browser as ``applicationServerKey`` and the
    private one signs every push, so halves pasted from two different
    ``gen_vapid_keys.sh`` runs are individually valid, boot cleanly, and are
    then rejected by the push service on every single send. Better a server
    that refuses to start than one that looks healthy and cannot notify anyone.
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

    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from py_vapid import Vapid02

    try:
        vapid = Vapid02.from_string(private_key)
        derived_public_bytes = vapid.public_key.public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        )
    except Exception as exc:
        raise ValueError(f"could not load the private key: {exc}") from exc

    if derived_public_bytes != public_bytes:
        raise ValueError(
            "VAPID_PUBLIC_KEY is not the public key belonging to VAPID_PRIVATE_KEY. "
            "Both halves have to come from the same key pair (one run of "
            "gen_vapid_keys.sh); a mismatched pair is rejected by every push "
            "service on every send."
        )


def _b64url_decode(value: str, name: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except Exception as exc:
        raise ValueError(f"{name} is not valid base64url: {exc}") from exc
