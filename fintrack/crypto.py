"""Encryption of Plaid access tokens at rest.

Access tokens are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) before
being written to SQLite. The Fernet key lives in the macOS Keychain (via the
`keyring` library), never on disk. Someone who copies the database file alone
cannot use the tokens.

The key is created once by `fintrack init`. At runtime a missing key is an
error rather than a reason to make a new one, because a new key could not
decrypt tokens already stored. That situation means re-linking every Item,
which would burn scarce Plaid Items.
"""

from __future__ import annotations

from pathlib import Path

import keyring
from cryptography.fernet import Fernet, InvalidToken

KEYRING_SERVICE = "fintrack"


class KeyMissingError(Exception):
    pass


def _keyring_username(home: Path) -> str:
    # Scope the key to the data directory so a test or second install
    # can never read or overwrite the real key.
    return f"token-key:{home}"


def ensure_key(home: Path) -> bool:
    """Create the encryption key in the Keychain if absent. Returns True if created."""
    username = _keyring_username(home)
    if keyring.get_password(KEYRING_SERVICE, username):
        return False
    keyring.set_password(KEYRING_SERVICE, username, Fernet.generate_key().decode())
    return True


def has_key(home: Path) -> bool:
    return keyring.get_password(KEYRING_SERVICE, _keyring_username(home)) is not None


class TokenCipher:
    """Encrypts and decrypts access tokens using the Keychain-held key."""

    def __init__(self, home: Path):
        key = keyring.get_password(KEYRING_SERVICE, _keyring_username(home))
        if not key:
            raise KeyMissingError(
                "Token encryption key not found in the Keychain. Run `fintrack init`. "
                "If tokens were already stored, they cannot be recovered without the original key."
            )
        self._fernet = Fernet(key.encode())

    def encrypt(self, token: str) -> bytes:
        return self._fernet.encrypt(token.encode())

    def decrypt(self, blob: bytes) -> str:
        try:
            return self._fernet.decrypt(blob).decode()
        except InvalidToken:
            raise KeyMissingError("Stored token could not be decrypted with the Keychain key.") from None
