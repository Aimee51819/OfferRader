"""Review queue: side-by-side evidence, candidates, approve/edit/reassign/reject/undo."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Application, EmailMessage, ParsedMessage, ReviewProposal
from ..schemas import ReviewApproveIn, ReviewRejectIn
from ..services.pipeline import review as svc
from .deps import not_found

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


def _out(db: Session, p: ReviewProposal) -> dict:
    msg = db.get(EmailMessage, p.email_message_id) if p.email_message_id else None
    parsed = db.get(ParsedMessage, p.parsed_message_id) if p.parsed_message_id else None
    target = db.get(Application, p.target_application_id) if p.target_application_id else None
    return {
        "id": p.id,
        "proposal_type": p.proposal_type,
        "state": p.state,
        "reason": p.reason,
        "confidence": p.confidence,
        "created_at": p.created_at,
        "reviewed_at": p.reviewed_at,
        "review_notes": p.review_notes,
        "proposed_changes": p.proposed_changes,
        "alternatives": p.alternatives or [],
        "target": {"id": target.id, "company": target.company.canonical_name, "role_title": target.role_title,
                   "stage": target.stage, "external_job_id": target.external_job_id} if target else None,
        "email": {
            "id": msg.id,
            "subject": msg.subject,
            "sender_name": msg.sender_name,
            "sender_address": msg.sender_address,
            "received_at": msg.received_at,
            "excerpt": msg.evidence_excerpt,
            "web_link": msg.web_link,
        } if msg else None,
        "parser": {"model": parsed.model_name, "prompt_version": parsed.prompt_version,
                   "schema_version": parsed.schema_version, "notes": parsed.validation_errors} if parsed else None,
    }


@router.get("")
def list_reviews(state: str = "pending", db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        select(ReviewProposal).where(ReviewProposal.state == state).order_by(ReviewProposal.created_at.desc())
    ).scalars().all()
    return [_out(db, p) for p in rows]


@router.get("/{proposal_id}")
def get_review(proposal_id: str, db: Session = Depends(get_db)) -> dict:
    p = db.get(ReviewProposal, proposal_id)
    if p is None:
        raise not_found("proposal not found")
    return _out(db, p)


def _act(db: Session, fn, *args, **kw) -> dict:
    try:
        p = fn(db, *args, **kw)
        db.commit()
    except (svc.ReviewError, ValueError) as exc:
        db.rollback()
        raise HTTPException(409, str(exc))
    return _out(db, p)


@router.post("/{proposal_id}/approve")
def approve(proposal_id: str, body: ReviewApproveIn, db: Session = Depends(get_db)) -> dict:
    return _act(db, svc.approve, proposal_id, target_application_id=body.target_application_id,
                create_new=body.create_new, edits=body.edits, notes=body.notes)


@router.post("/{proposal_id}/reject")
def reject(proposal_id: str, body: ReviewRejectIn, db: Session = Depends(get_db)) -> dict:
    return _act(db, svc.reject, proposal_id, notes=body.notes)


@router.post("/{proposal_id}/undo")
def undo(proposal_id: str, db: Session = Depends(get_db)) -> dict:
    return _act(db, svc.undo, proposal_id)
