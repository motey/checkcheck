#!/usr/bin/env python
"""Generate a VAPID key pair for Web Push (chunk P1 of the system-notifications
plan).

Prints ``VAPID_PUBLIC_KEY`` / ``VAPID_PRIVATE_KEY`` lines ready to paste into
``config.yml`` or the environment. Run it once per instance; the key pair
identifies this server to the push services it calls (see
``VAPID_CONTACT_EMAIL`` in ``config.py``) and should not be rotated casually,
since rotating it invalidates every subscription a browser has made against
the old public key.

    ./gen_vapid_keys.sh          # root wrapper around this script

Both values are base64url-encoded, unpadded: the public key is the raw
uncompressed P-256 point (65 bytes), the private key the raw 32-byte scalar.
That is exactly the shape both ``pywebpush``/``py_vapid`` (server side) and the
browser's ``PushManager.subscribe({applicationServerKey: ...})`` (client side)
expect, so no further conversion is needed on either end.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def main() -> None:
    private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
    public_key = private_key.public_key()

    private_value = private_key.private_numbers().private_value
    private_bytes = private_value.to_bytes(32, byteorder="big")
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    print(f"VAPID_PUBLIC_KEY={_b64url(public_bytes)}")
    print(f"VAPID_PRIVATE_KEY={_b64url(private_bytes)}")


if __name__ == "__main__":
    main()
