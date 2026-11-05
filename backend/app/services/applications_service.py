"""Manual / import / agent application writes, plus API serializers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.duplicates import DuplicateCheck, check_duplicate
from ..domain.enums import TERMINAL_STAGES
from ..domain.normalize import canonical_url, detect_ats, infer_category, normalize_req_id, normalize_title
from ..domain.resolver import get_or_create_company
from ..models import (
    ActionItem,
    Application,
    ApplicationEvent,
    Assessment,
    EmailMessage,
    Interview,
    ResumeVersion,
    ReviewProposal,
    utcnow,
)
from .notifications import urgency
from .pipeline.apply import audit

URGENCY_ORDER = ["overdue", "within_3h", "within_24h", "within_72h", "later", "no_reliable_deadline"]


class DuplicateBlocked(Exception):
    def __init__(self, check: DuplicateCheck) -> None:
        super().__init__("duplicate")
        self.check = check


def create_application(
    db: Session,
    *,
    company: str,
    role_title: str,
    external_job_id: str | None = None,
    posting_url: str | None = None,
    recruiting_cycle: str | None = None,
    category: str | None = None,
    location: str | None = None,
    application_channel: str | None = None,
    resume_version_id: str | None = None,
    stage: str = "tracked",
    submitted_at: datetime | None = None,
    submission_evidence_type: str | None = None,
    submission_receipt_id: str | None = None,
    submission_idempotency_key: str | None = None,
    notes: str | None = None,
    source: str = "manual",
    actor: str = "user",
    override_duplicate: bool = False,
    override_reason: str | None = None,
    source_reference: str | None = None,
) -> tuple[Application, DuplicateCheck]:
    check = check_duplicate(
        db,
        company_name=company,
        role_title=role_title,
        external_job_id=external_job_id,
        posting_url=posting_url,
        recruiting_cycle=recruiting_cycle,
    )
    if check.decision == "BLOCK" and not override_duplicate:
        raise DuplicateBlocked(check)
    comp = get_or_create_company(db, company)
    if resume_version_id and db.get(ResumeVersion, resume_version_id) is None:
        raise ValueError("resume_version_id not found")
    if submitted_at is not None and submitted_at.tzinfo is None:
        raise ValueError("submitted_at must include a timezone")
    if submitted_at is not None and submitted_at > utcnow() + timedelta(days=1):
        raise ValueError("submitted_at is in the future; record a submission only after it happened")
    app = Application(
        company_id=comp.id,
        role_title=role_title.strip(),
        normalized_role_title=normalize_title(role_title),
        external_job_id=normalize_req_id(external_job_id),
        posting_url=posting_url,
        canonical_posting_url=canonical_url(posting_url),
        ats_platform=detect_ats(posting_url),
        recruiting_cycle=recruiting_cycle,
        category=category or infer_category(role_title),
        location=location,
        application_channel=application_channel,
        resume_version_id=resume_version_id,
        stage=stage,
        stage_updated_at=utcnow(),
        stage_evidence_at=submitted_at,
        submitted_at=submitted_at,
        submission_evidence_type=submission_evidence_type if submitted_at else None,
        submission_receipt_id=submission_receipt_id,
        submission_idempotency_key=submission_idempotency_key,
        notes=notes,
        source_reference=source_reference or source,
    )
    db.add(app)
    db.flush()
    audit(
        db,
        application_id=app.id,
        event_type="application_created" if not submitted_at else "submission_recorded",
        description=(
            f"Created ({source}). "
            + (f"Submission evidence: {submission_evidence_type}." if submitted_at else "No submission evidence yet.")
        ),
        actor=actor,
        source=source,
        to_stage=stage,
        key=f"create:{app.id}",
        event_at=submitted_at or utcnow(),
    )
    if check.decision != "ALLOW" or override_duplicate:
        audit(
            db,
            application_id=app.id,
            event_type="duplicate_override" if override_duplicate else "duplicate_warning",
            description=(f"Duplicate check {check.decision}: " + " | ".join(check.reasons))[:1000]
            + (f" Override reason: {override_reason}" if override_reason else ""),
            actor=actor,
            source=source,
            key=f"dup:{app.id}",
            locator={"matches": check.matches},
        )
    return app, check


def set_stage(db: Session, app: Application, stage: str, *, note: str | None, lock: bool | None, actor: str = "user",
              event_at: datetime | None = None) -> ApplicationEvent:
    prev = app.stage
    app.stage = stage
    app.stage_updated_at = utcnow()
    when = event_at or utcnow()
    app.stage_evidence_at = max(filter(None, [app.stage_evidence_at, when]))
    if lock is not None:
        app.manual_stage_lock = lock
    if stage in TERMINAL_STAGES:
        for item in db.execute(
            select(ActionItem).where(ActionItem.application_id == app.id, ActionItem.state.in_(("open", "needs_review")))
        ).scalars():
            item.state = "cancelled"
    return audit(
        db,
        application_id=app.id,
        event_type="manual_stage_change",
        description=(note or f"Stage set to {stage} manually.") + (" (pinned)" if app.manual_stage_lock else ""),
        actor=actor,
        source="manual",
        from_stage=prev,
        to_stage=stage,
        key=f"manual-stage:{app.id}:{utcnow().isoformat()}",
        event_at=when,
    )


def mark_submitted(db: Session, app: Application, *, submitted_at: datetime, evidence: str, actor: str = "user",
                   receipt_id: str | None = None) -> None:
    app.submitted_at = submitted_at
    app.submission_evidence_type = evidence
    if receipt_id:
        app.submission_receipt_id = receipt_id
    if app.stage in {"tracked", "ready"}:
        app.stage = "submitted"
        app.stage_updated_at = utcnow()
    audit(
        db,
        application_id=app.id,
        event_type="submission_recorded",
        description=f"Submission confirmed ({evidence}).",
        actor=actor,
        source="manual" if evidence == "manual_confirmed" else "agent",
        to_stage=app.stage,
        key=f"submitted:{app.id}:{utcnow().isoformat()}",
        event_at=submitted_at,
    )


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def action_out(item: ActionItem, now: datetime | None = None, db: Session | None = None) -> dict:
    data = {c.name: getattr(item, c.name) for c in ActionItem.__table__.columns}
    data["urgency"] = urgency(item, now)
    app = item.application
    data["company"] = app.company.canonical_name if app else None
    data["role_title"] = app.role_title if app else None
    if db is not None and item.source_event_id:
        ev = db.get(ApplicationEvent, item.source_event_id)
        if ev and ev.email_message_id:
            msg = db.get(EmailMessage, ev.email_message_id)
            if msg:
                data["source_email_subject"] = msg.subject
                data["source_email_link"] = msg.web_link
    return data


def _source_of(app: Application) -> str:
    ref = app.source_reference or ""
    for s in ("email", "import", "agent", "manual", "legacy"):
        if ref.startswith(s):
            return s
    return "manual"


def application_out(db: Session, app: Application, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    open_items = db.execute(
        select(ActionItem).where(ActionItem.application_id == app.id, ActionItem.state.in_(("open", "needs_review")))
    ).scalars().all()
    nxt = None
    if open_items:
        def key(i: ActionItem):
            u = urgency(i, now)
            ref = i.due_at.isoformat() if i.due_at else (i.due_date or "9999")
            return (URGENCY_ORDER.index(u) if u in URGENCY_ORDER else 9, ref)

        nxt = action_out(sorted(open_items, key=key)[0], now)
    pending = db.scalar(
        select(func.count()).select_from(ReviewProposal).where(
            ReviewProposal.target_application_id == app.id, ReviewProposal.state == "pending"
        )
    )
    return {
        **{c.name: getattr(app, c.name) for c in Application.__table__.columns},
        "company": app.company.canonical_name,
        "resume_label": app.resume_version.label if app.resume_version else None,
        "verified": app.is_verified_submission,
        "next_action": nxt,
        "pending_reviews": pending or 0,
        "source": _source_of(app),
    }


def application_detail(db: Session, app: Application) -> dict:
    base = application_out(db, app)
    evs = db.execute(
        select(ApplicationEvent).where(ApplicationEvent.application_id == app.id).order_by(ApplicationEvent.event_at.desc(), ApplicationEvent.recorded_at.desc())
    ).scalars().all()
    msg_ids = [e.email_message_id for e in evs if e.email_message_id]
    msgs = (
        db.execute(select(EmailMessage).where(EmailMessage.id.in_(msg_ids))).scalars().all() if msg_ids else []
    )
    proposals = db.execute(
        select(ReviewProposal).where(ReviewProposal.target_application_id == app.id).order_by(ReviewProposal.created_at.desc())
    ).scalars().all()
    return {
        **base,
        "events": evs,
        "assessments": db.execute(select(Assessment).where(Assessment.application_id == app.id)).scalars().all(),
        "interviews": db.execute(select(Interview).where(Interview.application_id == app.id)).scalars().all(),
        "action_items": [
            action_out(i, db=db)
            for i in db.execute(select(ActionItem).where(ActionItem.application_id == app.id).order_by(ActionItem.created_at)).scalars()
        ],
        "evidence": [
            {
                "email_message_id": m.id,
                "subject": m.subject,
                "sender": f"{m.sender_name or ''} <{m.sender_address or ''}>".strip(),
                "received_at": m.received_at,
                "excerpt": m.evidence_excerpt,
                "web_link": m.web_link,
            }
            for m in sorted(msgs, key=lambda m: m.received_at or utcnow())
        ],
        "proposals": [
            {"id": p.id, "state": p.state, "proposal_type": p.proposal_type, "reason": p.reason, "created_at": p.created_at}
            for p in proposals
        ],
    }
