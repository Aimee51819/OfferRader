"""Idempotent storage of fetched messages + encrypted local body cache."""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import settings
from ...core.security import decrypt, encrypt
from ...models import EmailAccount, EmailMessage
from ..extraction.extractor import prepare_body
from ..mail.base import ProviderMessage


def _cache_dir() -> Path:
    path = settings.data_path / "body_cache"
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def cache_body(message_id: str, body: str) -> str:
    ref = f"{message_id}.fernet"
    (_cache_dir() / ref).write_text(encrypt(body or " ") or "", encoding="utf-8")
    return ref


def load_cached_body(ref: str | None) -> str | None:
    if not ref:
        return None
    path = _cache_dir() / Path(ref).name  # never follow paths from the DB
    if not path.exists():
        return None
    try:
        return decrypt(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def drop_cached_body(ref: str | None) -> None:
    if not ref:
        return
    path = _cache_dir() / Path(ref).name
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def body_hash(body: str) -> str:
    return hashlib.sha256((body or "").encode("utf-8", "replace")).hexdigest()


def ingest_message(db: Session, account: EmailAccount, pm: ProviderMessage) -> tuple[EmailMessage, bool]:
    """Store one message. Returns (row, created). Duplicate provider ids are no-ops."""
    existing = db.execute(
        select(EmailMessage).where(
            EmailMessage.email_account_id == account.id,
            EmailMessage.provider_message_id == pm.provider_message_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    body = (pm.body_text or "")[: settings.max_body_bytes]
    row = EmailMessage(
        email_account_id=account.id,
        provider_message_id=pm.provider_message_id,
        provider_thread_id=pm.thread_id,
        internet_message_id=pm.internet_message_id,
        sender_address=(pm.sender_address or "")[:320] or None,
        sender_name=(pm.sender_name or "")[:200] or None,
        recipient_address=(pm.recipient_address or "")[:320] or None,
        subject=(pm.subject or "")[:500],
        sent_at=pm.sent_at,
        received_at=pm.received_at or pm.sent_at,
        body_hash=body_hash(body),
        evidence_excerpt=prepare_body(body, max_chars=1500),
        web_link=pm.web_link,
        processing_state="pending",
    )
    db.add(row)
    db.flush()
    row.body_cache_ref = cache_body(row.id, body)
    return row, True
