from __future__ import annotations

import logging

from cryptography.fernet import Fernet

from ..config import settings

logger = logging.getLogger("tracker.security")

_KEYRING_SERVICE = "AppTracker"
_KEYRING_USER = "fernet-key"


def _keyring_get() -> bytes | None:
    """Read the key from the OS keychain if the optional `keyring` package is installed."""
    try:
        import keyring  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return None
    try:
        value = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
    except Exception:  # noqa: BLE001 - no backend (headless Linux, CI)
        return None
    return value.encode() if value else None


def _keyring_set(key: bytes) -> bool:
    try:
        import keyring  # type: ignore[import-not-found]

        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, key.decode())
        return True
    except Exception:  # noqa: BLE001
        return False


def _load_or_create_key() -> bytes:
    """Return the Fernet key: APP_SECRET_KEY > existing key file > OS keychain > new."""
    if settings.app_secret_key:
        return settings.app_secret_key.encode()

    key_file = settings.secret_key_file
    if key_file.exists():
        # Keep honoring an existing file so legacy encrypted values stay readable.
        return key_file.read_bytes()

    if settings.use_keychain:
        stored = _keyring_get()
        if stored:
            return stored

    key = Fernet.generate_key()
    if settings.use_keychain and _keyring_set(key):
        logger.info("Stored encryption key in the OS keychain.")
        return key
    key_file.write_bytes(key)
    try:
        key_file.chmod(0o600)
    except OSError:
        pass
    return key


_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt(plaintext: str | None) -> str | None:
    """Encrypt a secret for storage. Returns None for empty input."""
    if not plaintext:
        return None
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str | None) -> str | None:
    """Decrypt a stored secret. Returns None for empty input."""
    if not ciphertext:
        return None
    return _get_fernet().decrypt(ciphertext.encode()).decode()
