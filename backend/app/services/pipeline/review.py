"""Human review actions. Every action writes an audit event and is idempotent."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import ActionItem, Application, ApplicationEvent, Assessment, Interview, ReviewProposal, utcnow
from .apply import apply_plan, audit, create_application_from_plan


class ReviewError(Exception):
    pass


def _get(db: Session, proposal_id: str) -> ReviewProposal:
    p = db.get(ReviewProposal, proposal_id)
    if p is None:
        raise ReviewError("proposal not found")
    return p


def approve(
    db: Session,
    proposal_id: str,
    *,
    target_application_id: str | None = None,
    create_new: bool = False,
    edits: dict | None = None,
    notes: str | None = None,
    actor: str = "user",
) -> ReviewProposal:
    """Approve (optionally edited / reassigned). Re-approving an approved proposal is a no-op."""
    p = _get(db, proposal_id)
    if p.state in {"approved", "edited"}:
        return p
    if p.state == "rejected":
        raise ReviewError("proposal was rejected; undo the rejection first")
    plan = dict(p.proposed_changes)
    edits = dict(edits or {})
    edits["actor"] = actor
    target_id = target_application_id or (None if create_new else p.target_application_id)
    if create_new or (target_id is None and plan.get("create_application")):
        app = create_application_from_plan(db, plan, edits)
    elif target_id:
        app = db.get(Application, target_id)
        if app is None:
            raise ReviewError("target application not found")
    else:
        raise ReviewError("choose an application or create a new one")
    reassigned = bool(target_application_id and target_application_id != p.target_application_id)
    event_ids = apply_plan(db, plan, app, actor=actor, human_approved=True, edits=edits)
    edited = bool({k for k in edits if k != "actor"}) or reassigned
    p.state = "edited" if edited else "approved"
    p.target_application_id = app.id
    p.reviewed_at = utcnow()
    p.review_notes = notes
    p.applied_event_ids = event_ids
    audit(
        db,
        application_id=app.id,
        event_type="review_" + p.state,
        description=(
            f"{'Reassigned and approved' if reassigned else p.state.title()} proposal "
            f"({p.proposal_type}). {notes or ''}"
        ).strip(),
        actor=actor,
        key=f"review:{p.id}:{p.state}:{len(event_ids)}:{utcnow().isoformat()}",
        email_message_id=p.email_message_id,
        locator={"proposal_id": p.id, "edits": {k: v for k, v in edits.items() if k != "actor"}},
    )
    db.flush()
    return p


def reject(db: Session, proposal_id: str, *, notes: str | None = None, actor: str = "user") -> ReviewProposal:
    p = _get(db, proposal_id)
    if p.state == "rejected":
        return p
    if p.state in {"approved", "edited"}:
        raise ReviewError("proposal already applied; use undo")
    p.state = "rejected"
    p.reviewed_at = utcnow()
    p.review_notes = notes
    audit(
        db,
        application_id=p.target_application_id,
        event_type="review_rejected",
        description=f"Rejected proposal ({p.proposal_type}). {notes or ''}".strip(),
        actor=actor,
        key=f"review:{p.id}:rejected:{utcnow().isoformat()}",
        email_message_id=p.email_message_id,
        locator={"proposal_id": p.id},
    )
    db.flush()
    return p


def undo(db: Session, proposal_id: str, *, actor: str = "user") -> ReviewProposal:
    """Reverse an approval/rejection with new audit records; history is never deleted."""
    p = _get(db, proposal_id)
    if p.state == "pending":
        return p
    if p.state == "rejected":
        p.state = "pending"
        p.reviewed_at = None
        audit(
            db,
            application_id=p.target_application_id,
            event_type="undo",
            description="Undid rejection; proposal is pending again.",
            actor=actor,
            key=f"undo:{p.id}:{utcnow().isoformat()}",
            locator={"proposal_id": p.id},
        )
        db.flush()
        return p

    events = [db.get(ApplicationEvent, eid) for eid in (p.applied_event_ids or [])]
    events = [e for e in events if e is not None]
    app = db.get(Application, p.target_application_id) if p.target_application_id else None
    # Restore the stage only if nothing newer changed it since.
    stage_events = [e for e in events if e.to_stage]
    if app is not None and stage_events:
        last = stage_events[-1]
        first = stage_events[0]
        if app.stage == last.to_stage and first.from_stage:
            prev_stage = app.stage
            app.stage = first.from_stage
            app.stage_updated_at = utcnow()
            prior = db.execute(
                select(ApplicationEvent)
                .where(
                    ApplicationEvent.application_id == app.id,
                    ApplicationEvent.to_stage.is_not(None),
                    ApplicationEvent.id.not_in([e.id for e in events]),
                    ApplicationEvent.event_type != "undo",
                )
                .order_by(ApplicationEvent.event_at.desc())
            ).scalars().first()
            app.stage_evidence_at = prior.event_at if prior else None
            audit(
                db,
                application_id=app.id,
                event_type="undo",
                description=f"Stage restored {prev_stage} -> {app.stage} after undo.",
                actor=actor,
                from_stage=prev_stage,
                to_stage=app.stage,
                key=f"undo-stage:{p.id}:{utcnow().isoformat()}",
                locator={"proposal_id": p.id},
            )
    if app is not None:
        acks = [e for e in events if e.event_type in {"application_confirmation", "application_received"}]
        if acks and app.submission_evidence_type == "acknowledgment_email" and app.submitted_at in {
            e.event_at for e in acks
        }:
            app.submitted_at = None
            app.acknowledged_at = None
            app.submission_evidence_type = None
    for e in events:
        audit(
            db,
            application_id=e.application_id,
            event_type="undo",
            description=f"Undid '{e.event_type}' from proposal review.",
            actor=actor,
            supersedes=e.id,
            key=f"undo:{e.id}:{utcnow().isoformat()}",
            locator={"proposal_id": p.id},
        )
        for item in db.execute(select(ActionItem).where(ActionItem.source_event_id == e.id)).scalars():
            if item.state in {"open", "needs_review"}:
                item.state = "cancelled"
        for a in db.execute(select(Assessment).where(Assessment.invitation_event_id == e.id)).scalars():
            a.state = "cancelled"
        for iv in db.execute(select(Interview).where(Interview.source_event_id == e.id)).scalars():
            iv.state = "cancelled"
    p.state = "pending"
    p.reviewed_at = None
    p.applied_event_ids = None
    # Back to what the pipeline originally proposed (a reassignment is undone too).
    p.target_application_id = (p.proposed_changes or {}).get("target_application_id")
    db.flush()
    return p
