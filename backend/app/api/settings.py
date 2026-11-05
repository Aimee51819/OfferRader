"""Settings, notification lead times, local model health."""
from __future__ import annotations

from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import NotificationRule
from ..schemas import NotificationRuleIn, NotificationRuleOut, SettingsUpdate
from ..services import settings_service
from ..services.llm.ollama import LLMError, default_backend
from ..services.mail import oauth

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)) -> dict:
    data = settings_service.all_settings(db)
    data["oauth"] = {
        "gmail_configured": oauth.is_configured("gmail"),
        "outlook_configured": oauth.is_configured("outlook"),
        "gmail_redirect_uri": oauth.redirect_uri("gmail"),
        "outlook_redirect_uri": oauth.redirect_uri("outlook"),
    }
    return data


@router.put("/settings")
def put_settings(body: SettingsUpdate, db: Session = Depends(get_db)) -> dict:
    changes = body.model_dump(exclude_unset=True)
    if "display_timezone" in changes:
        try:
            ZoneInfo(changes["display_timezone"])
        except Exception:  # noqa: BLE001
            raise HTTPException(422, "Unknown IANA timezone")
    if "ollama_base_url" in changes:
        from ..services.llm.ollama import assert_local_url

        try:
            assert_local_url(changes["ollama_base_url"])
        except LLMError as exc:
            raise HTTPException(422, str(exc))
    for k, v in changes.items():
        settings_service.set_value(db, k, v)
    db.commit()
    if "poll_interval_seconds" in changes:
        from ..scheduler import reschedule

        reschedule(int(changes["poll_interval_seconds"]))
    return get_settings(db)


@router.get("/notification-rules", response_model=list[NotificationRuleOut])
def rules(db: Session = Depends(get_db)) -> list:
    return db.execute(select(NotificationRule).order_by(NotificationRule.lead_time_minutes.desc())).scalars().all()


@router.put("/notification-rules", response_model=list[NotificationRuleOut])
def replace_rules(body: list[NotificationRuleIn], db: Session = Depends(get_db)) -> list:
    existing = {r.lead_time_minutes: r for r in db.execute(select(NotificationRule)).scalars()}
    keep = set()
    for r in body:
        row = existing.get(r.lead_time_minutes)
        if row is None:
            row = NotificationRule(kind="deadline", lead_time_minutes=r.lead_time_minutes)
            db.add(row)
        row.enabled, row.channel = r.enabled, r.channel
        keep.add(r.lead_time_minutes)
    for minutes, row in existing.items():
        if minutes not in keep:
            row.enabled = False  # disabled, not deleted, so the reminder log stays consistent
    db.commit()
    return rules(db)


@router.get("/llm/health")
def llm_health(db: Session = Depends(get_db)) -> dict:
    try:
        backend = default_backend(
            model=settings_service.get(db, "ollama_model"), base_url=settings_service.get(db, "ollama_base_url")
        )
    except LLMError as exc:
        return {"ok": False, "reachable": False, "error": str(exc)}
    return backend.health()


@router.get("/integrations/token-hint")
def token_hint(db: Session = Depends(get_db)) -> dict:
    """Where to find the integration token (never returns the token itself)."""
    settings_service.integration_token(db)
    return {
        "header": "Authorization: Bearer <token>",
        "how_to_read": "cd backend && .venv/bin/python -m app.cli integration-token",
    }
