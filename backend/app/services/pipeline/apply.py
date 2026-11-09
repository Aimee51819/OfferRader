"""Turn validated extractions into plans, and apply plans to one application.

A *plan* is a JSON-serializable dict stored in ``review_proposals.proposed_changes``
(or applied directly under the auto policy). Applying a plan is idempotent:
each event has a stable idempotency key derived from the source message, so
re-processing or re-approving never duplicates history, assessments, or
action items.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...domain.deadlines import normalize_deadline, parse_event_time
from ...domain.enums import EVENT_TO_STAGE, NOISE_EVENT_TYPES, SENSITIVE_EVENT_TYPES
from ...domain.extraction_schema import Extraction
from ...domain.normalize import (
    canonical_url,
    detect_ats,
    domain_of,
    infer_category,
    normalize_req_id,
    normalize_title,
)
from ...domain.resolver import get_or_create_company, match_application, resolve_company
from ...domain.stages import decide_stage
from ...models import (
    ActionItem,
    Application,
    ApplicationEvent,
    Assessment,
    EmailMessage,
    Interview,
    utcnow,
)

logger = logging.getLogger("tracker.apply")

HARMLESS_EVENT_TYPES = {
    "application_confirmation",
    "application_received",
    "assessment_invitation",
    "assessment_reminder",
    "assessment_completed",
    "screening",
    "interview_invitation",
    "interview_rescheduled",
    "portal_update",
}

_LABELS = {
    "application_confirmation": "Application confirmed",
    "application_received": "Application received",
    "assessment_invitation": "Assessment invitation",
    "assessment_reminder": "Assessment reminder",
    "assessment_completed": "Assessment completed",
    "screening": "Recruiter screen",
    "interview_invitation": "Interview invitation",
    "interview_rescheduled": "Interview rescheduled",
    "interview_cancelled": "Interview cancelled",
    "rejection": "Rejection",
    "offer": "Offer",
    "withdrawal": "Withdrawal",
    "portal_update": "Portal update",
}


def message_identity(msg: EmailMessage) -> str:
    """Stable across accounts when the same email lands in two inboxes."""
    if msg.internet_message_id:
        return f"imid:{msg.internet_message_id.strip().strip('<>').lower()}"
    return f"acct:{msg.email_account_id}:{msg.provider_message_id}"


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def build_plan(db: Session, msg: EmailMessage, ext: Extraction) -> dict:
    received = msg.received_at or msg.sent_at
    sender_domain = domain_of(msg.sender_address)
    company_res = resolve_company(
        db, ext.company.name, sender_domain=sender_domain, company_domain=ext.company.domain
    )
    match = match_application(
        db,
        company=company_res.company,
        role_title=ext.role.title,
        requisition_id=ext.role.requisition_id,
        job_url=ext.role.job_url,
        thread_id=msg.provider_thread_id,
        account_id=msg.email_account_id,
        event_at=received,
    )
    flags: list[str] = list(ext.ambiguities)
    events: list[dict] = []
    for idx, ev in enumerate(ext.events):
        if ev.type in NOISE_EVENT_TYPES:
            continue
        item: dict = {
            "index": idx,
            "type": ev.type,
            "label": _LABELS.get(ev.type, ev.type),
            "event_at": _iso(received),
            "implied_stage": EVENT_TO_STAGE.get(ev.type),
            "action_required": ev.action_required,
            "assessment_type": ev.assessment_type,
            "assessment_provider": ev.assessment_provider,
            "action_url": ev.action_url,
            "evidence_quote": ev.evidence_quotes[0] if ev.evidence_quotes else None,
            "evidence_quotes": ev.evidence_quotes,
            "confidence": ev.confidence,
            "deadline": None,
            "interview_time": None,
        }
        if ev.deadline and (ev.deadline.raw_expression or ev.deadline.date_only):
            d = normalize_deadline(
                ev.deadline.raw_expression,
                received_at=msg.received_at,
                sent_at=msg.sent_at,
                timezone_hint=ev.deadline.timezone_text,
                date_only_hint=ev.deadline.date_only,
                anchor_hint=ev.deadline.anchor,
            )
            item["deadline"] = d.as_dict()
            if d.needs_review:
                flags.append(f"{item['label']}: deadline needs review ({'; '.join(d.assumptions)})")
        if ev.type in {"interview_invitation", "interview_rescheduled", "screening"} and (
            ev.event_time_raw or ev.event_time
        ):
            t = parse_event_time(ev.event_time_raw or ev.event_time, received_at=msg.received_at)
            item["interview_time"] = t.as_dict()
            if t.needs_review or t.precision != "instant":
                flags.append(f"{item['label']}: time needs review ({'; '.join(t.assumptions)})")
        events.append(item)

    plan = {
        "message_identity": message_identity(msg),
        "email_message_id": msg.id,
        "company": {
            "name": ext.company.name,
            "domain": ext.company.domain,
            "company_id": company_res.company.id if company_res.company else None,
            "resolved_name": company_res.company.canonical_name if company_res.company else None,
            "resolution": company_res.reason,
            "ambiguous": company_res.ambiguous,
            "confidence": ext.company.confidence,
        },
        "role": {
            "title": ext.role.title,
            "requisition_id": ext.role.requisition_id,
            "job_url": ext.role.job_url,
            "confidence": ext.role.confidence,
        },
        "match": {
            "decision": match.decision,
            "application_id": match.best.id if match.best else None,
            "score": match.score,
            "reason": match.reason,
        },
        "target_application_id": match.best.id if match.best else None,
        "create_application": match.decision == "none",
        "events": events,
        "conflicts": [],
        "flags": flags,
        "overall_confidence": ext.overall_confidence,
        "model_needs_review": ext.needs_review,
        "alternatives": match.alternatives(),
        "sender_domain": sender_domain,
    }
    if match.best is not None:
        plan["conflicts"] = find_deadline_conflicts(db, match.best, events)
    return plan


def _deadline_key(d: dict | None) -> tuple:
    if not d:
        return (None, None)
    return (d.get("due_at"), d.get("due_date"))


def find_deadline_conflicts(db: Session, app: Application, events: list[dict]) -> list[dict]:
    conflicts = []
    open_items = db.execute(
        select(ActionItem).where(
            ActionItem.application_id == app.id,
            ActionItem.kind == "oa_deadline",
            ActionItem.state.in_(("open", "needs_review")),
        )
    ).scalars().all()
    for ev in events:
        if ev["type"] != "assessment_reminder" or not ev.get("deadline"):
            continue
        new = _deadline_key(ev["deadline"])
        if new == (None, None):
            continue
        for item in open_items:
            old = (_iso(item.due_at), item.due_date)
            if old == (None, None):
                continue
            if old != new:
                conflicts.append(
                    {
                        "action_item_id": item.id,
                        "existing": {
                            "due_at": old[0],
                            "due_date": old[1],
                            "raw": item.due_expression_raw,
                            "evidence": item.evidence_quote,
                        },
                        "new": {**ev["deadline"], "evidence": ev.get("evidence_quote")},
                        "event_index": ev["index"],
                    }
                )
    return conflicts


def route_plan(plan: dict, *, policy: str, min_confidence: float, app: Application | None) -> tuple[bool, str, str]:
    """Return (auto_apply, proposal_type, reason)."""
    if plan["create_application"]:
        return False, "create_application", "No existing application matches; confirm before tracking."
    if plan["match"]["decision"] != "auto":
        return False, "match", plan["match"]["reason"]
    if plan["conflicts"]:
        return False, "deadline", "A reminder states a deadline that differs from the one on file."
    sensitive = [e for e in plan["events"] if e["type"] in SENSITIVE_EVENT_TYPES]
    if sensitive:
        return False, "status", f"{sensitive[0]['label']} is a terminal decision; review before applying."
    if app is not None:
        for e in plan["events"]:
            d = decide_stage(
                current_stage=app.stage,
                stage_evidence_at=app.stage_evidence_at,
                manual_lock=app.manual_stage_lock,
                event_type=e["type"],
                event_at=_dt(e["event_at"]) or utcnow(),
                source="email",
            )
            if d.needs_review:
                return False, "status", d.reason
    if plan["flags"] or plan["model_needs_review"]:
        kind = "deadline" if any("deadline" in f or "time needs" in f for f in plan["flags"]) else "other"
        return False, kind, "; ".join(plan["flags"])[:500] or "Model flagged this message for review."
    if policy != "auto_harmless":
        return False, "status", "Review-first policy is on."
    if any(e["type"] not in HARMLESS_EVENT_TYPES for e in plan["events"]):
        return False, "status", "Event type requires review."
    if plan["overall_confidence"] < min_confidence or any(
        e["confidence"] < min_confidence for e in plan["events"]
    ):
        return False, "status", f"Confidence below {min_confidence:.2f}."
    return True, "status", "High-confidence, unambiguous, non-terminal update."


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def _event_key(plan: dict, ev: dict, revision: int) -> str:
    base = f"email:{plan['message_identity']}:{ev['index']}:{ev['type']}"
    return base if revision == 0 else f"{base}:r{revision}"


def audit(
    db: Session,
    *,
    application_id: str | None,
    event_type: str,
    description: str,
    actor: str = "user",
    source: str = "manual",
    key: str | None = None,
    from_stage: str | None = None,
    to_stage: str | None = None,
    email_message_id: str | None = None,
    supersedes: str | None = None,
    evidence_quote: str | None = None,
    locator: dict | None = None,
    event_at: datetime | None = None,
) -> ApplicationEvent:
    ev = ApplicationEvent(
        application_id=application_id,
        email_message_id=email_message_id,
        event_type=event_type,
        source=source,
        event_at=event_at or utcnow(),
        from_stage=from_stage,
        to_stage=to_stage,
        description=description,
        is_approved=True,
        idempotency_key=key or f"{event_type}:{datetime.now(timezone.utc).isoformat()}:{id(object())}",
        actor=actor,
        supersedes_event_id=supersedes,
        evidence_quote=evidence_quote,
        evidence_locator=locator,
    )
    db.add(ev)
    db.flush()
    return ev


def create_application_from_plan(db: Session, plan: dict, edits: dict | None = None) -> Application:
    edits = edits or {}
    company_name = edits.get("company") or plan["company"].get("resolved_name") or plan["company"].get("name")
    if not company_name:
        raise ValueError("Cannot create an application without a company name; edit the proposal first.")
    title = edits.get("role_title") or plan["role"].get("title")
    if not title:
        raise ValueError("Cannot create an application without a role title; edit the proposal first.")
    company = get_or_create_company(db, company_name, domain=plan.get("sender_domain"))
    req = normalize_req_id(edits.get("external_job_id") or plan["role"].get("requisition_id"))
    url = edits.get("posting_url") or plan["role"].get("job_url")
    first_at = _dt(plan["events"][0]["event_at"]) if plan["events"] else utcnow()
    app = Application(
        company_id=company.id,
        role_title=title,
        normalized_role_title=normalize_title(title),
        external_job_id=req,
        posting_url=url,
        canonical_posting_url=canonical_url(url),
        ats_platform=detect_ats(url, plan.get("sender_domain")),
        recruiting_cycle=edits.get("recruiting_cycle"),
        category=edits.get("category") or infer_category(title),
        stage="tracked",
        stage_updated_at=first_at or utcnow(),
        source_reference=f"email:{plan['message_identity']}",
    )
    db.add(app)
    db.flush()
    audit(
        db,
        application_id=app.id,
        event_type="application_created",
        description="Created from an email after human review.",
        actor=edits.get("actor", "user"),
        source="email",
        key=f"create:{plan['message_identity']}:{app.id}",
        email_message_id=plan.get("email_message_id"),
        event_at=first_at,
    )
    return app


def apply_plan(
    db: Session,
    plan: dict,
    app: Application,
    *,
    actor: str,
    human_approved: bool,
    edits: dict | None = None,
) -> list[str]:
    """Apply every event in the plan to ``app``. Returns ids of created events."""
    edits = edits or {}
    created: list[str] = []
    conflict_choice = edits.get("conflict_resolution", "use_new")
    conflict_items = {c["action_item_id"]: c for c in plan.get("conflicts", [])}
    for ev in plan["events"]:
        etype = edits.get("event_types", {}).get(str(ev["index"]), ev["type"])
        revision = 0
        key = _event_key(plan, {**ev, "type": etype}, 0)
        while True:
            existing = db.execute(
                select(ApplicationEvent).where(ApplicationEvent.idempotency_key == key)
            ).scalar_one_or_none()
            if existing is None:
                break
            if existing.application_id == app.id and not _is_undone(db, existing):
                key = None  # already applied to this application: idempotent no-op
                break
            revision += 1
            key = _event_key(plan, {**ev, "type": etype}, revision)
        if key is None:
            continue
        event_at = _dt(ev["event_at"]) or utcnow()
        decision = decide_stage(
            current_stage=app.stage,
            stage_evidence_at=app.stage_evidence_at,
            manual_lock=app.manual_stage_lock and not edits.get("override_lock"),
            event_type=etype,
            event_at=event_at,
            source="email",
        )
        new_stage = decision.new_stage
        if decision.needs_review and not human_approved:
            new_stage = None
        if edits.get("stage"):
            new_stage = edits["stage"]
        from_stage = app.stage
        row = ApplicationEvent(
            application_id=app.id,
            email_message_id=plan.get("email_message_id"),
            event_type=etype,
            source="email",
            event_at=event_at,
            from_stage=from_stage if new_stage else None,
            to_stage=new_stage,
            description=_describe(ev, etype, decision.reason, new_stage),
            confidence=ev.get("confidence"),
            evidence_quote=ev.get("evidence_quote"),
            evidence_locator={
                "email_message_id": plan.get("email_message_id"),
                "message_identity": plan["message_identity"],
                "event_index": ev["index"],
                "quotes": ev.get("evidence_quotes", []),
            },
            is_approved=True,
            idempotency_key=key,
            actor=actor,
        )
        db.add(row)
        db.flush()
        created.append(row.id)
        if new_stage:
            app.stage = new_stage
            app.stage_updated_at = utcnow()
            if app.stage_evidence_at is None or event_at >= app.stage_evidence_at:
                app.stage_evidence_at = event_at
        if etype in {"application_confirmation", "application_received"}:
            if app.acknowledged_at is None or event_at < app.acknowledged_at:
                app.acknowledged_at = event_at
            if app.submitted_at is None:
                app.submitted_at = event_at
                app.submission_evidence_type = app.submission_evidence_type or "acknowledgment_email"
        _side_effects(db, app, ev, etype, row, conflict_choice, conflict_items)
    app.updated_at = utcnow()
    db.flush()
    return created


def _is_undone(db: Session, event: ApplicationEvent) -> bool:
    return (
        db.execute(
            select(ApplicationEvent.id).where(
                ApplicationEvent.supersedes_event_id == event.id,
                ApplicationEvent.event_type == "undo",
            )
        ).first()
        is not None
    )


def _describe(ev: dict, etype: str, reason: str, new_stage: str | None) -> str:
    label = _LABELS.get(etype, etype)
    parts = [label]
    if ev.get("action_required"):
        parts.append(ev["action_required"])
    if new_stage:
        parts.append(f"stage -> {new_stage}")
    elif reason and EVENT_TO_STAGE.get(etype):
        parts.append(f"stage unchanged: {reason}")
    return ". ".join(parts)[:1000]


def _deadline_fields(d: dict | None, received: datetime | None) -> dict:
    if not d:
        return {
            "due_at": None,
            "due_date": None,
            "due_timezone": None,
            "due_precision": "unknown",
            "due_expression_raw": None,
            "due_anchor": None,
            "deadline_assumption": "No deadline was stated in the email.",
            "deadline_confidence": None,
            "state": "open",
        }
    precision = d.get("precision") or "unknown"
    due_at = _dt(d.get("due_at"))
    if precision == "relative" and due_at is None:
        precision = "unknown"
    anchor = d.get("anchor")
    if anchor and received and anchor.startswith("email_"):
        anchor = f"{anchor}={received.astimezone(timezone.utc).isoformat()}"
    return {
        "due_at": due_at,
        "due_date": d.get("due_date"),
        "due_timezone": d.get("due_timezone"),
        "due_precision": precision if precision in {"instant", "day", "relative", "unknown"} else "unknown",
        "due_expression_raw": d.get("raw"),
        "due_anchor": anchor,
        "deadline_assumption": "; ".join(d.get("assumptions") or []) or None,
        "deadline_confidence": d.get("confidence"),
        "state": "needs_review" if d.get("needs_review") else "open",
    }


def _side_effects(
    db: Session,
    app: Application,
    ev: dict,
    etype: str,
    row: ApplicationEvent,
    conflict_choice: str,
    conflict_items: dict,
) -> None:
    received = row.event_at
    if etype in {"assessment_invitation", "assessment_reminder"}:
        assessment = None
        if etype == "assessment_reminder":
            assessment = db.execute(
                select(Assessment)
                .where(Assessment.application_id == app.id, Assessment.state.in_(("invited", "started")))
                .order_by(Assessment.invited_at.desc())
            ).scalars().first()
        if assessment is None:
            assessment = Assessment(
                application_id=app.id,
                invitation_event_id=row.id,
                provider=ev.get("assessment_provider"),
                assessment_type=ev.get("assessment_type") or "OA",
                assessment_url=ev.get("action_url"),
                invited_at=received,
                state="invited",
            )
            db.add(assessment)
            db.flush()
        fields = _deadline_fields(ev.get("deadline"), received)
        if etype == "assessment_reminder":
            relevant = [c for c in conflict_items.values() if c["event_index"] == ev["index"]]
            if relevant:
                if conflict_choice == "keep_existing":
                    return
                for c in relevant:
                    old = db.get(ActionItem, c["action_item_id"])
                    if old is not None and old.state in {"open", "needs_review"}:
                        old.state = "cancelled"
                        old.instruction = (old.instruction or "") + " [Superseded by a reminder deadline after review.]"
            else:
                same = db.execute(
                    select(ActionItem).where(
                        ActionItem.assessment_id == assessment.id,
                        ActionItem.state.in_(("open", "needs_review")),
                    )
                ).scalars().first()
                if same is not None and (fields["due_at"] is None and fields["due_date"] is None):
                    return  # reminder without a deadline: nothing new to track
                if same is not None and (_iso(same.due_at), same.due_date) == (
                    _iso(fields["due_at"]),
                    fields["due_date"],
                ):
                    return  # same deadline restated
        title = f"{assessment.provider + ' ' if assessment.provider else ''}{ev.get('assessment_type') or 'OA'}"
        db.add(
            ActionItem(
                application_id=app.id,
                assessment_id=assessment.id,
                source_event_id=row.id,
                kind="oa_deadline",
                title=f"Complete {title.strip()}",
                instruction=ev.get("action_required"),
                evidence_quote=(ev.get("deadline") or {}).get("raw") or ev.get("evidence_quote"),
                **fields,
            )
        )
    elif etype == "assessment_completed":
        for a in db.execute(
            select(Assessment).where(Assessment.application_id == app.id, Assessment.state.in_(("invited", "started")))
        ).scalars():
            a.state = "completed"
            a.completed_at = received
        _close_items(db, app, kinds=("oa_deadline",), state="completed")
    elif etype in {"interview_invitation", "interview_rescheduled", "screening"}:
        t = ev.get("interview_time") or {}
        starts = _dt(t.get("due_at")) if t.get("precision") == "instant" else None
        if etype == "interview_rescheduled":
            prev = db.execute(
                select(Interview)
                .where(Interview.application_id == app.id, Interview.state.in_(("proposed", "scheduled")))
                .order_by(Interview.created_at.desc())
            ).scalars().first()
            if prev is not None:
                prev.state = "rescheduled"
                _close_items(db, app, kinds=("interview",), state="cancelled")
        iv = Interview(
            application_id=app.id,
            source_event_id=row.id,
            round_label="Recruiter screen" if etype == "screening" else None,
            starts_at=starts,
            original_timezone=t.get("due_timezone"),
            location_or_video_url=ev.get("action_url"),
            state="scheduled" if starts else "proposed",
        )
        db.add(iv)
        db.flush()
        if starts:
            db.add(
                ActionItem(
                    application_id=app.id,
                    interview_id=iv.id,
                    source_event_id=row.id,
                    kind="interview",
                    title="Attend " + ("recruiter screen" if etype == "screening" else "interview"),
                    instruction=ev.get("action_required"),
                    due_at=starts,
                    due_timezone=t.get("due_timezone"),
                    due_precision="instant",
                    due_expression_raw=t.get("raw"),
                    deadline_assumption="; ".join(t.get("assumptions") or []) or None,
                    deadline_confidence=t.get("confidence"),
                    evidence_quote=t.get("raw") or ev.get("evidence_quote"),
                    state="open",
                )
            )
        elif ev.get("action_required") or ev.get("deadline"):
            fields = _deadline_fields(ev.get("deadline"), received)
            db.add(
                ActionItem(
                    application_id=app.id,
                    interview_id=iv.id,
                    source_event_id=row.id,
                    kind="schedule_interview",
                    title="Schedule " + ("recruiter screen" if etype == "screening" else "interview"),
                    instruction=ev.get("action_required"),
                    evidence_quote=ev.get("evidence_quote"),
                    **fields,
                )
            )
    elif etype == "interview_cancelled":
        for iv in db.execute(
            select(Interview).where(Interview.application_id == app.id, Interview.state.in_(("proposed", "scheduled")))
        ).scalars():
            iv.state = "cancelled"
        _close_items(db, app, kinds=("interview", "schedule_interview"), state="cancelled")
    elif etype in {"rejection", "withdrawal"}:
        _close_items(db, app, kinds=None, state="cancelled")
        for a in db.execute(
            select(Assessment).where(Assessment.application_id == app.id, Assessment.state.in_(("invited", "started")))
        ).scalars():
            a.state = "cancelled"
    elif etype in {"application_confirmation", "application_received", "portal_update"}:
        d = ev.get("deadline") or {}
        if d.get("due_at") or d.get("due_date"):
            # A date the employer gave for its own reply ("we'll get back to you within
            # 72 hours"), or a step it asks for after applying. Tracked as feedback, not OA.
            fields = _deadline_fields(d, received)
            fields["state"] = "open"
            db.add(
                ActionItem(
                    application_id=app.id,
                    source_event_id=row.id,
                    # An explicit request ("complete your application within 72 hours") is a
                    # to-do for the candidate; otherwise it is the employer's reply window.
                    kind="todo" if ev.get("action_required") else "feedback",
                    title=ev.get("action_required") or "Expected response",
                    instruction=ev.get("action_required"),
                    evidence_quote=d.get("raw") or ev.get("evidence_quote"),
                    **fields,
                )
            )


def _close_items(db: Session, app: Application, *, kinds: tuple[str, ...] | None, state: str) -> None:
    stmt = select(ActionItem).where(
        ActionItem.application_id == app.id, ActionItem.state.in_(("open", "needs_review"))
    )
    if kinds:
        stmt = stmt.where(ActionItem.kind.in_(kinds))
    for item in db.execute(stmt).scalars():
        item.state = state
        if state == "completed":
            item.completed_at = utcnow()


def deadline_preview(plan: dict) -> list[dict]:
    """Deadlines inside a pending plan (shown on the dashboard as 'pending review')."""
    out = []
    for ev in plan.get("events", []):
        d = ev.get("deadline")
        if d and (d.get("due_at") or d.get("due_date")):
            out.append({"event_type": ev["type"], **d})
        t = ev.get("interview_time")
        if t and t.get("due_at"):
            out.append({"event_type": ev["type"], **t})
    return out

