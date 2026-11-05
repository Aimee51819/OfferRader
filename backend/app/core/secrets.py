"""Encrypted credential storage.

Tokens and passwords are stored as Fernet ciphertext in the ``credentials``
table. ``email_accounts.credential_ref`` holds only the row id. The Fernet key
comes from (in order): APP_SECRET_KEY, the OS keychain via the optional
``keyring`` package (macOS Keychain on a Mac), or DATA_DIR/secret.key (0600).
Plaintext never appears in logs, API responses, or SQLite.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..models import Credential
from .security import decrypt, encrypt


def store_secret(db: Session, kind: str, value: str | dict, *, ref: str | None = None) -> str:
    payload = value if isinstance(value, str) else json.dumps(value)
    ciphertext = encrypt(payload)
    assert ciphertext is not None
    if ref:
        row = db.get(Credential, ref)
        if row is not None:
            row.ciphertext = ciphertext
            db.flush()
            return row.id
    row = Credential(kind=kind, ciphertext=ciphertext)
    db.add(row)
    db.flush()
    return row.id


def load_secret(db: Session, ref: str | None) -> str | None:
    if not ref:
        return None
    row = db.get(Credential, ref)
    if row is None:
        return None
    try:
        return decrypt(row.ciphertext)
    except Exception:  # noqa: BLE001 - wrong key / corrupted -> treat as missing
        return None


def load_json_secret(db: Session, ref: str | None) -> dict[str, Any] | None:
    raw = load_secret(db, ref)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def delete_secret(db: Session, ref: str | None) -> None:
    if not ref:
        return
    row = db.get(Credential, ref)
    if row is not None:
        db.delete(row)
        db.flush()


_SECRET_KEYS = {"access_token", "refresh_token", "id_token", "password", "client_secret", "code"}


def redact(obj: Any) -> Any:
    """Recursively blank secret-looking fields before logging anything."""
    if isinstance(obj, dict):
        return {
            k: ("***" if k.lower() in _SECRET_KEYS or "token" in k.lower() else redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj
