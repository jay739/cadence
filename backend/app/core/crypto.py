"""Symmetric encryption for integration secrets stored at rest in the SQLite database.

Uses Fernet (AES-128-CBC + HMAC-SHA256, authenticated) from the `cryptography` package, keyed
by `Settings.resolved_settings_encryption_key()` (see app.core.config for why that key is a
sibling of, not derived from, SESSION_SECRET). Every secret field written to the
`integration_settings` table goes through `encrypt_value()` before the INSERT/UPDATE and
`decrypt_value()` after the SELECT; no plaintext secret is ever written to disk.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class DecryptionError(Exception):
    """Raised when a stored ciphertext cannot be decrypted with the current key.

    This can happen if the encryption key file was lost/rotated out from under an existing
    database. Callers should treat this the same as "value unavailable" (never crash the
    settings page over it) and surface it as a configuration problem for the admin to fix by
    re-entering the secret.
    """


def encrypt_value(plaintext: str, *, key: bytes) -> str:
    """Encrypt a plaintext string, returning a str (base64 token) safe for a TEXT column."""
    fernet = Fernet(key)
    return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_value(token: str, *, key: bytes) -> str:
    """Decrypt a stored token back to its original plaintext string."""
    fernet = Fernet(key)
    try:
        return fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise DecryptionError("Stored value could not be decrypted") from exc


def mask_secret(plaintext: str | None) -> str | None:
    """Return a masked display form of a secret, e.g. "ab12" -> shown as "••••ab12".

    Never returns the real value. Short secrets (<=4 chars) are fully masked rather than
    risking revealing the whole thing via the "last 4 chars" convention.
    """
    if not plaintext:
        return None
    if len(plaintext) <= 4:
        return "•" * 8
    return "•" * 8 + plaintext[-4:]
