"""Typed key/value settings stored in the ``settings`` table."""
from __future__ import annotations

import json
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings as env
from ..models import NotificationRule, Setting

DEFAULTS: dict[str, Any] = {
    # review_first: every email-derived change waits for approval.
    # auto_harmless: high-confidence, non-sensitive, unambiguous changes apply directly.
    # auto_all (default): every recognised application email is applied directly; the
    # dashboard is the place to correct or remove anything wrong.
    "auto_apply_policy": "auto_all",
    "auto_apply_min_confidence": 0.9,
    "poll_interval_seconds": env.email_poll_interval_seconds,
    # Automatic background sync stays off until the user runs a dry-run and enables it.
    "auto_sync_enabled": False,
    "initial_lookback_days": 60,
    "display_timezone": env.display_timezone,
    "desktop_notifications": False,
    "ollama_model": env.ollama_model,
    "ollama_base_url": env.ollama_base_url,
    # Warn when no successful sync for this long (Mac asleep, scheduler stopped).
    "missed_sync_warning_minutes": 120,
}

DEFAULT_LEAD_TIMES_MIN = (72 * 60, 24 * 60, 3 * 60)


def get(db: Session, key: str) -> Any:
    row = db.get(Setting, key)
    if row is None or row.value is None:
        return DEFAULTS.get(key)
    try:
        return json.loads(row.value)
    except json.JSONDecodeError:
        return row.value


def set_value(db: Session, key: str, value: Any) -> None:
    row = db.get(Setting, key)
    encoded = json.dumps(value)
    if row is None:
        db.add(Setting(key=key, value=encoded))
    else:
        row.value = encoded


def all_settings(db: Session) -> dict[str, Any]:
    return {k: get(db, k) for k in DEFAULTS}


def integration_token(db: Session) -> str:
    """Token for local integrations. Env wins; else generated once and stored."""
    if env.integration_token:
        return env.integration_token
    from ..core.secrets import load_secret, store_secret

    ref = get(db, "integration_token_ref")
    if ref:
        token = load_secret(db, ref)
        if token:
            return token
    token = secrets.token_urlsafe(32)
    ref = store_secret(db, "integration", token)
    set_value(db, "integration_token_ref", ref)
    db.commit()
    return token


def seed_defaults(db: Session) -> None:
    existing = db.execute(select(NotificationRule)).scalars().first()
    if existing is None:
        for minutes in DEFAULT_LEAD_TIMES_MIN:
            db.add(NotificationRule(kind="deadline", lead_time_minutes=minutes, channel="dashboard"))
        db.commit()
