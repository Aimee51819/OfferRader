"""Deadline urgency + reminders (dashboard and optional macOS desktop banner).

No email is ever sent. Each (action item, rule, scheduled time) fires once
(unique constraint). After a long sleep only the most urgent passed threshold
fires; the skipped ones are logged as ``skipped`` so they never fire later.

Date-only deadlines have no clock time. For *scheduling reminders only* they
are treated as due at 00:00 America/New_York on that date so a reminder is
early rather than late; the stored deadline stays date-only.
"""
from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import ActionItem, NotificationLog, NotificationRule, utcnow
from . import settings_service

logger = logging.getLogger("tracker.notify")
ET = ZoneInfo("America/New_York")


def reminder_reference(item: ActionItem) -> datetime | None:
    if item.due_at is not None:
        return item.due_at
    if item.due_date:
        d = date.fromisoformat(item.due_date)
        return datetime(d.year, d.month, d.day, tzinfo=ET).astimezone(timezone.utc)
    return None


def urgency(item: ActionItem, now: datetime | None = None) -> str:
    now = now or utcnow()
    if item.state not in {"open", "needs_review"}:
        return item.state
    if item.due_at is not None:
        delta = item.due_at - now
        if delta.total_seconds() < 0:
            return "overdue"
        hours = delta.total_seconds() / 3600
        if hours <= 3:
            return "within_3h"
        if hours <= 24:
            return "within_24h"
        if hours <= 72:
            return "within_72h"
        return "later"
    if item.due_date:
        today = now.astimezone(ET).date()
        d = date.fromisoformat(item.due_date)
        if d < today:
            return "overdue"
        if d == today:
            return "within_24h"  # some time today; exact time unknown
        if d <= today + timedelta(days=3):
            return "within_72h"
        return "later"
    return "no_reliable_deadline"


def _desktop(title: str, text: str) -> str:
    if platform.system() != "Darwin" or not shutil.which("osascript"):
        return "unsupported"
    safe_t = title.replace('"', "'")[:80]
    safe_x = text.replace('"', "'")[:180]
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{safe_x}" with title "{safe_t}"'],
            check=True,
            timeout=10,
            capture_output=True,
        )
        return "delivered"
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Desktop notification failed: %s", type(exc).__name__)
        return "failed"


def evaluate(db: Session, now: datetime | None = None) -> list[NotificationLog]:
    now = now or utcnow()
    rules = db.execute(select(NotificationRule).where(NotificationRule.enabled.is_(True))).scalars().all()
    if not rules:
        return []
    rules = sorted(rules, key=lambda r: r.lead_time_minutes)  # most urgent first
    desktop = bool(settings_service.get(db, "desktop_notifications"))
    items = db.execute(
        select(ActionItem).where(ActionItem.state.in_(("open", "needs_review")))
    ).scalars().all()
    fired: list[NotificationLog] = []
    for item in items:
        ref = reminder_reference(item)
        if ref is None or ref < now:
            continue
        passed = [r for r in rules if now >= ref - timedelta(minutes=r.lead_time_minutes)]
        if not passed:
            continue
        chosen = passed[0]
        for rule in passed:
            scheduled = ref - timedelta(minutes=rule.lead_time_minutes)
            exists = db.execute(
                select(NotificationLog.id).where(
                    NotificationLog.action_item_id == item.id,
                    NotificationLog.rule_id == rule.id,
                    NotificationLog.scheduled_for_at == scheduled,
                )
            ).first()
            if exists:
                continue
            state = "pending" if rule is chosen else "skipped"
            if rule is chosen and (desktop or rule.channel == "desktop"):
                company = item.application.company.canonical_name if item.application else ""
                state = _desktop(
                    f"{item.title} — {company}",
                    f"Due in about {max(1, round((ref - now).total_seconds() / 3600))}h. Open AppTracker for details.",
                )
            elif rule is chosen:
                state = "delivered"  # shown on the dashboard
            log = NotificationLog(
                action_item_id=item.id, rule_id=rule.id, scheduled_for_at=scheduled, delivery_state=state
            )
            try:
                with db.begin_nested():
                    db.add(log)
                if state != "skipped":
                    fired.append(log)
            except IntegrityError:
                continue  # another tick already logged it
    db.commit()
    return fired
