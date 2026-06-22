"""Hashing primitives for local-account secrets.

Two very different kinds of secret are hashed here:

* **Passwords / passphrases** are user-chosen and low-entropy, so they get a
  slow, memory-hard hash (argon2, via ``pwdlib``) to resist brute force. argon2
  salts every hash internally, so callers must *not* pre-salt the input — doing
  so is both redundant and, with the old bcrypt scheme, was what pushed inputs
  past bcrypt's 72-byte limit.
* **API tokens** are long, high-entropy random strings (``secrets.token_urlsafe``),
  so a single SHA-256 is already infeasible to brute-force and is far cheaper
  than a KDF. SHA-256 also has no length ceiling.

This module is intentionally free of any auth/business logic so it can be shared
by both the account-auth model and the public-share passphrase code without
creating a dependency between them.
"""

import hashlib
import hmac

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

# argon2id with pwdlib's sensible defaults.
password_hasher = PasswordHash((Argon2Hasher(),))


def hash_password(password: str) -> str:
    """Hash a user-chosen password/passphrase with argon2. Never log the input."""
    return password_hasher.hash(password)


def verify_password(password: str | None, password_hash: str | None) -> bool:
    """Constant-time check of a password against its argon2 hash.

    Returns ``False`` (rather than raising) when either side is missing, so
    callers can treat "no password set" as simply "does not verify".
    """
    if not password or not password_hash:
        return False
    return password_hasher.verify(password, password_hash)


def hash_api_token(token: str) -> str:
    """Hash a high-entropy API token with SHA-256 (no per-row salt needed)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_api_token(token: str | None, token_hash: str | None) -> bool:
    """Constant-time check of an API token against its SHA-256 hash."""
    if not token or not token_hash:
        return False
    return hmac.compare_digest(hash_api_token(token), token_hash)
