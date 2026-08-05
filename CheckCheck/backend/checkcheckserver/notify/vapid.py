"""What key this instance signs push messages with (chunk K1 of
``docs/plans/PUSH_KEYS_AND_SHARE_SETTING.md``).

The one place that answers that question. Before this chunk both read sites
(``notify/push.py`` at send time, ``api/routes/routes_public_config.py`` for the
browser) read ``Config.VAPID_*`` directly, which meant push only worked on an
instance where somebody had run ``gen_vapid_keys.sh`` and pasted three settings.
Now:

1. **Configured keys win.** ``VAPID_PUBLIC_KEY`` / ``VAPID_PRIVATE_KEY`` keep
   their boot validation exactly as it was, and when they are set nothing is
   generated and nothing is written to the database (decision 3 of the plan).
   That is also what keeps the dev scripts and the E2E harness, which pin their
   own throwaway pair, working unchanged.
2. **Otherwise the instance's own pair**, read from ``instance_secret``.
3. **Otherwise a fresh pair**, generated here and stored, so the *next* boot
   takes branch 2.

A VAPID pair is a P-256 keypair. It has no relationship to the deployment's URL
or its certificate and costs about a millisecond to make, so there is nothing to
gate generation on (decision 1): the secure-context requirement that actually
governs whether a browser will subscribe is the browser's, on the page calling
``PushManager.subscribe``, and no server-side check can help with it.

**Resolved once at startup, not per request.** ``notify/dispatcher.py``'s
lifespan calls :func:`resolve_at_startup` after the engine exists, and the two
read sites take the answer from the module-level holder below. That is what lets
``routes_public_config.py`` stay a session-free endpoint, and it keeps the
generating INSERT off the request path.

**Losing the row unsubscribes every device.** A database restored without
``instance_secret`` gets a fresh pair on the next boot, and every existing
``push_subscription`` then fails with a 401/403 from its push service, which
chunk N3 deliberately classifies as "not the device's fault" and so fails the
outbox row permanently instead of deleting the subscription. Documented next to
the backup guidance in ``docs/UPGRADING.md``.

**Note on the module import order.** Kept out of ``Config``'s import graph on
purpose: ``notify/push.py`` is imported from inside ``Config``'s own boot
validator, and this module imports the database layer, which builds a ``Config``
of its own. Nothing here may be reached from a validator; generation belongs in
the lifespan, after the engine exists.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Optional

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


log = get_logger()


# The two ``instance_secret.name`` values this module owns. Stable strings: they
# are the primary key of the rows a live instance already has.
PUBLIC_KEY_SECRET_NAME = "vapid_public_key"
PRIVATE_KEY_SECRET_NAME = "vapid_private_key"


@dataclass(frozen=True)
class VapidKeys:
    """The pair this instance signs with, and where it came from."""

    public_key: str
    private_key: str
    # The VAPID JWT's ``sub`` claim, already prefixed (``mailto:...`` or a URL).
    # Part of the signature, so it belongs with the keys rather than being
    # rebuilt at each send site.
    subject: str
    # True when the pair came from ``VAPID_PUBLIC_KEY``/``VAPID_PRIVATE_KEY``
    # rather than from the database. Only used for logging and for the tests
    # that assert configured keys are not overwritten.
    configured: bool


_resolved: Optional[VapidKeys] = None


def generate_key_pair() -> tuple[str, str]:
    """A fresh P-256 VAPID pair as ``(public_key, private_key)``, base64url.

    ``py_vapid``'s ``Vapid02`` is the same library the boot check and every send
    already load, so this adds no dependency. Both halves are encoded the way
    ``gen_vapid_keys.sh`` prints them and the way every other part of this
    codebase expects to read them: unpadded base64url over the raw uncompressed
    point and the raw 32-byte scalar.
    """
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from py_vapid import Vapid02

    vapid = Vapid02()
    vapid.generate_keys()
    public_bytes = vapid.public_key.public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )
    # The raw 32-byte scalar, which is what `validate_vapid_keys` checks for and
    # what `Vapid02.from_string` reads back. `private_bytes()` would give a DER
    # or PEM blob instead.
    private_bytes = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    return _b64url(public_bytes), _b64url(private_bytes)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def resolve_subject(config: Config) -> str:
    """The VAPID JWT's ``sub`` claim for this instance.

    RFC 8292 wants contact information for whoever runs the application server,
    as a ``mailto:`` or an ``https:`` URI, so a push service can reach an
    operator about abuse. ``VAPID_CONTACT_EMAIL`` is that, and stays the
    operator's answer when they gave one. Without it, rather than refusing to
    start (which is the behaviour this chunk exists to remove) the instance
    falls back to something true about itself: the administrator's address if
    the deployment set one, otherwise its own public URL, which is a valid
    ``sub`` and identifies the instance to anybody who receives its pushes.
    """
    if config.VAPID_CONTACT_EMAIL:
        return f"mailto:{config.VAPID_CONTACT_EMAIL}"
    if config.ADMIN_USER_EMAIL:
        return f"mailto:{config.ADMIN_USER_EMAIL}"
    return config.get_server_url()


def configured_keys(config: Config) -> Optional[VapidKeys]:
    """The operator's own pair, or None when they did not configure one.

    Decision 3 of the plan: when both halves are set they win outright, nothing
    is generated and nothing is written to the database. Their shape and their
    correspondence were already checked at boot by ``Config._validate_push``.
    """
    if not (config.VAPID_PUBLIC_KEY and config.VAPID_PRIVATE_KEY):
        return None
    return VapidKeys(
        public_key=config.VAPID_PUBLIC_KEY,
        private_key=config.VAPID_PRIVATE_KEY.get_secret_value(),
        subject=resolve_subject(config),
        configured=True,
    )


async def resolve(session, config: Optional[Config] = None) -> VapidKeys:
    """The keys this instance signs with, generating and storing a pair if needed.

    Takes a session rather than opening one, so the caller decides the
    transaction. Safe to call concurrently and from more than one replica: the
    INSERT is resolved by ``instance_secret``'s primary key and the loser reads
    the winner's row (see ``db/instance_secret.get_or_create``).
    """
    config = config or Config()

    configured = configured_keys(config)
    if configured is not None:
        return configured

    from checkcheckserver.db import instance_secret as instance_secret_db

    # Generated together and stored as two rows, because the two halves are
    # separately named secrets and because the private one is what a future
    # rotation would touch. The pair is made once and closed over, so the second
    # `get_or_create` cannot store the public half of a *different* pair than the
    # first stored: whichever call loses its race, both fall back to reading, and
    # a mismatch could only come from the two calls generating independently.
    fresh_public, fresh_private = generate_key_pair()
    private_key = await instance_secret_db.get_or_create(
        session, PRIVATE_KEY_SECRET_NAME, lambda: fresh_private
    )
    if private_key == fresh_private:
        public_key = await instance_secret_db.get_or_create(
            session, PUBLIC_KEY_SECRET_NAME, lambda: fresh_public
        )
    else:
        # Somebody else's private key won, so the public half has to be theirs
        # too. Derive it rather than trusting a stored row we may have raced
        # past: the private key is the whole of the pair, and deriving cannot
        # disagree with it.
        public_key = _public_key_for(private_key)
        await instance_secret_db.get_or_create(
            session, PUBLIC_KEY_SECRET_NAME, lambda: public_key
        )

    if public_key != _public_key_for(private_key):
        # Only reachable if somebody edited the table by hand. Signing with a
        # pair whose halves disagree is rejected by every push service on every
        # send, so say so loudly instead of pushing into a void.
        raise ValueError(
            "The stored VAPID key pair does not belong together: "
            f"{PUBLIC_KEY_SECRET_NAME!r} is not the public key of "
            f"{PRIVATE_KEY_SECRET_NAME!r}. Delete both rows of the "
            "instance_secret table to have a fresh pair generated (every "
            "subscribed device then has to be re-enabled)."
        )

    return VapidKeys(
        public_key=public_key,
        private_key=private_key,
        subject=resolve_subject(config),
        configured=False,
    )


def _public_key_for(private_key: str) -> str:
    """The base64url public half of *private_key*, derived rather than looked up."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from py_vapid import Vapid02

    vapid = Vapid02.from_string(private_key)
    return _b64url(
        vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    )


# ── the module-level holder the two read sites use ──────────────────────────


async def resolve_at_startup(config: Optional[Config] = None) -> Optional[VapidKeys]:
    """Fill the holder from the dispatcher's lifespan. Never raises.

    Called once per process, before any request is served. A failure here (a
    database that is not reachable yet, a hand-edited pair) must not stop the
    server from booting: everything except push still works, so it is logged and
    the holder stays empty, which reads to the rest of the app as "this instance
    has no push key", the same as an instance with push switched off.
    """
    global _resolved
    config = config or Config()
    if not config.NOTIFY_PUSH_ENABLED:
        return None

    from checkcheckserver.db._session import get_async_session_context

    try:
        async with get_async_session_context() as session:
            keys = await resolve(session, config)
    except Exception:
        log.exception(
            "[notify] could not resolve the instance's VAPID keys; push is unavailable "
            "until the next restart"
        )
        return None

    _resolved = keys
    if keys.configured:
        log.info("[notify] push signing with the configured VAPID key pair")
    else:
        log.info(
            "[notify] push signing with this instance's own generated VAPID key pair"
        )
    return keys


def get_keys() -> Optional[VapidKeys]:
    """The resolved pair, or None when this process has none.

    None means push cannot be sent or subscribed to: either the channel is off,
    or startup resolution failed. Both read sites treat it that way.
    """
    return _resolved


def keys_for(config: Config) -> Optional[VapidKeys]:
    """The pair to sign with, for a synchronous caller that cannot await.

    The holder first, since that is the one answer the whole process agrees on.
    Falling back to the configured pair covers a process that never ran the
    lifespan and so never filled the holder: a test driving ``_send_sync``
    directly, and an out-of-process drain
    (``NOTIFY_DISPATCH_IN_PROCESS=false``) that has not resolved for itself. It
    can only ever produce the *configured* pair, never a generated one, because
    generating needs a database and a decision about who wins the race, and
    guessing either from a send site is how two replicas end up signing with
    different keys.
    """
    resolved = get_keys()
    if resolved is not None:
        return resolved
    return configured_keys(config)


def set_keys(keys: Optional[VapidKeys]) -> None:
    """Install a pair directly. For tests, and for a caller driving the resolution
    itself instead of through the lifespan."""
    global _resolved
    _resolved = keys


def reset_keys() -> None:
    set_keys(None)
