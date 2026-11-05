"""Dashboard: summary cards, deadline buckets, changes since last sync, funnel, digest."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..domain.enums import SUBMITTED_OR_LATER, TERMINAL_STAGES
from ..models import (
    ActionItem,
    Application,
    ApplicationEvent,
    EmailAccount,
    EmailMessage,
    NotificationLog,
    ReviewProposal,
    SyncRun,
    utcnow,
)
from ..services import settings_service
from ..services.applications_service import action_out
from ..services.pipeline.apply import deadline_preview

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

BUCKETS = ["overdue", "within_3h", "within_24h", "within_72h", "later", "no_reliable_deadline"]


def sync_health(db: Session, now: datetime | None = None) -> dict:
    now = now or utcnow()
    accounts = db.execute(select(EmailAccount)).scalars().all()
    enabled = [a for a in accounts if a.enabled]
    last_ok = max((a.last_successful_sync_at for a in enabled if a.last_successful_sync_at), default=None)
    warn_after = int(settings_service.get(db, "missed_sync_warning_minutes"))
    auto = bool(settings_service.get(db, "auto_sync_enabled"))
    heartbeat = settings_service.get(db, "scheduler_heartbeat_at")
    hb = datetime.fromisoformat(heartbeat) if heartbeat else None
    warnings = []
    if enabled and auto and (last_ok is None or now - last_ok > timedelta(minutes=warn_after)):
        warnings.append(
            "No successful sync in "
            + (f"{int((now - last_ok).total_seconds() // 60)} minutes" if last_ok else "this install")
            + " — the Mac may have slept or the scheduler stopped. Sync will catch up from the saved position."
        )
    if hb and now - hb > timedelta(minutes=10):
        warnings.append("The background scheduler has not run for over 10 minutes (sleep or app stopped).")
    for a in enabled:
        if a.last_error_code == "auth_expired":
            warnings.append(f"{a.address}: mailbox authorization expired — reconnect in Settings.")
        elif a.last_error_code in {"llm_unavailable", "llm_model_missing", "llm_timeout"}:
            warnings.append("Local model unavailable — new mail is stored and will be parsed when Ollama is back.")
    last_run = db.execute(select(SyncRun).order_by(SyncRun.started_at.desc())).scalars().first()
    return {
        "accounts": len(accounts),
        "enabled_accounts": len(enabled),
        "auto_sync_enabled": auto,
        "last_successful_sync_at": last_ok,
        "last_run": {
            "status": last_run.status, "started_at": last_run.started_at, "finished_at": last_run.finished_at,
            "summary": last_run.safe_error_summary,
        } if last_run else None,
        "pending_messages": db.scalar(
            select(func.count()).select_from(EmailMessage).where(EmailMessage.processing_state.in_(("pending", "failed")))
        ),
        "warnings": list(dict.fromkeys(warnings)),
    }


@router.get("/summary")
def summary(db: Session = Depends(get_db)) -> dict:
    now = utcnow()
    apps = db.execute(select(Application)).scalars().all()
    items = db.execute(select(ActionItem).where(ActionItem.state.in_(("open", "needs_review")))).scalars().all()
    by_urgency = {b: 0 for b in BUCKETS}
    for i in items:
        by_urgency[action_out(i, now)["urgency"]] = by_urgency.get(action_out(i, now)["urgency"], 0) + 1
    stage_count = lambda *s: sum(1 for a in apps if a.stage in s)  # noqa: E731
    pending_oa = sum(1 for i in items if i.kind == "oa_deadline")
    return {
        "active_applications": sum(1 for a in apps if a.stage not in TERMINAL_STAGES and a.stage in SUBMITTED_OR_LATER),
        "tracked_not_submitted": stage_count("tracked", "ready"),
        "submitted_or_acknowledged": stage_count("submitted", "acknowledged"),
        "verified_submissions": sum(1 for a in apps if a.is_verified_submission),
        "unverified_records": sum(1 for a in apps if not a.is_verified_submission),
        "pending_oa": pending_oa,
        "deadlines_within_72h": by_urgency["within_3h"] + by_urgency["within_24h"] + by_urgency["within_72h"],
        "overdue": by_urgency["overdue"],
        "no_reliable_deadline": by_urgency["no_reliable_deadline"],
        "interviews": stage_count("screening", "interviewing"),
        "offers": stage_count("offer", "accepted"),
        "rejected": stage_count("rejected"),
        "review_needed": db.scalar(select(func.count()).select_from(ReviewProposal).where(ReviewProposal.state == "pending")),
        "sync": sync_health(db, now),
    }


@router.get("/deadlines")
def deadlines(db: Session = Depends(get_db)) -> dict:
    now = utcnow()
    items = db.execute(select(ActionItem).where(ActionItem.state.in_(("open", "needs_review")))).scalars().all()
    buckets: dict[str, list] = {b: [] for b in BUCKETS}
    for i in items:
        row = action_out(i, now, db)
        buckets.setdefault(row["urgency"], []).append(row)
    for rows in buckets.values():
        rows.sort(key=lambda r: (r["due_at"].isoformat() if r["due_at"] else (r["due_date"] or "9999")))
    # Deadlines that exist only inside a pending review: shown so nothing is missed.
    pending = []
    for p in db.execute(select(ReviewProposal).where(ReviewProposal.state == "pending")).scalars():
        for d in deadline_preview(p.proposed_changes or {}):
            pending.append({
                "proposal_id": p.id,
                "company": (p.proposed_changes.get("company") or {}).get("name"),
                "role_title": (p.proposed_changes.get("role") or {}).get("title"),
                **d,
            })
    pending.sort(key=lambda r: r.get("due_at") or r.get("due_date") or "9999")
    return {"buckets": buckets, "pending_review": pending, "now": now}


@router.get("/changes")
def changes(since: datetime | None = None, db: Session = Depends(get_db)) -> dict:
    """What changed since the previous completed sync (or ``since``)."""
    if since is None:
        runs = db.execute(
            select(SyncRun).where(SyncRun.finished_at.is_not(None)).order_by(SyncRun.finished_at.desc()).limit(2)
        ).scalars().all()
        since = runs[1].finished_at if len(runs) > 1 else (utcnow() - timedelta(days=7))
    evs = db.execute(
        select(ApplicationEvent).where(ApplicationEvent.recorded_at >= since).order_by(ApplicationEvent.recorded_at.desc()).limit(200)
    ).scalars().all()
    out = []
    for e in evs:
        app = db.get(Application, e.application_id) if e.application_id else None
        out.append({
            "id": e.id, "event_type": e.event_type, "description": e.description, "recorded_at": e.recorded_at,
            "event_at": e.event_at, "to_stage": e.to_stage, "actor": e.actor, "source": e.source,
            "application_id": e.application_id,
            "company": app.company.canonical_name if app else None, "role_title": app.role_title if app else None,
        })
    new_reviews = db.scalar(
        select(func.count()).select_from(ReviewProposal).where(ReviewProposal.created_at >= since, ReviewProposal.state == "pending")
    )
    return {"since": since, "events": out, "new_reviews": new_reviews}


@router.get("/funnel")
def funnel(db: Session = Depends(get_db)) -> dict:
    apps = db.execute(select(Application)).scalars().all()
    reached: dict[str, set] = {k: set() for k in ("submitted", "assessment", "screening", "interviewing", "offer")}
    for a in apps:
        stages = {a.stage}
        stages |= set(
            db.execute(select(ApplicationEvent.to_stage).where(ApplicationEvent.application_id == a.id, ApplicationEvent.to_stage.is_not(None))).scalars()
        )
        if a.is_verified_submission or stages & SUBMITTED_OR_LATER:
            reached["submitted"].add(a.id)
        if stages & {"assessment_invited", "assessment_completed"}:
            reached["assessment"].add(a.id)
        if "screening" in stages:
            reached["screening"].add(a.id)
        if "interviewing" in stages:
            reached["interviewing"].add(a.id)
        if stages & {"offer", "accepted"}:
            reached["offer"].add(a.id)
    by_category: dict[str, dict] = {}
    for a in apps:
        c = by_category.setdefault(a.category or "Other", {"total": 0, "active": 0, "rejected": 0, "offers": 0})
        c["total"] += 1
        c["active"] += a.stage not in TERMINAL_STAGES
        c["rejected"] += a.stage == "rejected"
        c["offers"] += a.stage in {"offer", "accepted"}
    return {"funnel": {k: len(v) for k, v in reached.items()}, "by_category": by_category, "total": len(apps)}


@router.get("/digest")
def digest(db: Session = Depends(get_db)) -> dict:
    now = utcnow()
    d = deadlines(db)
    soon = d["buckets"]["overdue"] + d["buckets"]["within_3h"] + d["buckets"]["within_24h"]
    day_ago = now - timedelta(days=1)
    evs = db.execute(select(ApplicationEvent).where(ApplicationEvent.recorded_at >= day_ago, ApplicationEvent.source == "email")).scalars().all()
    lines = []
    for r in soon:
        lines.append(f"Due {'(overdue) ' if r['urgency'] == 'overdue' else ''}{r['title']} — {r['company']}: {r['role_title']}")
    if d["pending_review"]:
        lines.append(f"{len(d['pending_review'])} deadline(s) are waiting in the review queue.")
    pending = db.scalar(select(func.count()).select_from(ReviewProposal).where(ReviewProposal.state == "pending"))
    return {
        "generated_at": now,
        "due_soon": soon,
        "email_events_last_24h": len(evs),
        "pending_reviews": pending,
        "lines": lines,
        "sync": sync_health(db, now),
    }


@router.get("/notifications")
def notifications(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        select(NotificationLog).where(NotificationLog.dismissed.is_(False), NotificationLog.delivery_state != "skipped")
        .order_by(NotificationLog.triggered_at.desc()).limit(50)
    ).scalars().all()
    out = []
    for n in rows:
        item = db.get(ActionItem, n.action_item_id)
        if item is None or item.state not in {"open", "needs_review"}:
            continue
        out.append({"id": n.id, "triggered_at": n.triggered_at, "delivery_state": n.delivery_state,
                    "action_item": action_out(item, db=db)})
    return out


@router.post("/notifications/{notification_id}/dismiss")
def dismiss(notification_id: str, db: Session = Depends(get_db)) -> dict:
    n = db.get(NotificationLog, notification_id)
    if n is not None:
        n.dismissed = True
        db.commit()
    return {"ok": True}


_ = timezone
