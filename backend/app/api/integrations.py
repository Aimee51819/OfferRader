"""Local integration endpoints for an approved application tool (career-ops / JobAppAgent).

This tracker never submits applications. A separate tool, after a human
approved and actually submitted an application, may POST a *confirmed*
receipt here. Requests need ``Authorization: Bearer <integration token>``;
the server binds to loopback only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..domain.duplicates import check_duplicate
from ..models import Application, ResumeVersion
from ..schemas import DuplicateCheckIn, DuplicateCheckOut, SubmissionReceiptIn, SubmissionReceiptOut
from ..services.applications_service import DuplicateBlocked, create_application, mark_submitted
from .deps import require_integration_token

router = APIRouter(prefix="/api/integrations", tags=["integrations"], dependencies=[Depends(require_integration_token)])


@router.post("/preflight", response_model=DuplicateCheckOut)
def preflight(body: DuplicateCheckIn, db: Session = Depends(get_db)) -> dict:
    """Call BEFORE asking the human to approve a submission."""
    return check_duplicate(db, company_name=body.company, role_title=body.role_title,
                           external_job_id=body.external_job_id, posting_url=body.posting_url,
                           recruiting_cycle=body.recruiting_cycle).as_dict()


@router.post("/submissions", response_model=SubmissionReceiptOut)
def record_submission(body: SubmissionReceiptIn, db: Session = Depends(get_db)) -> dict:
    if body.submitted_at.tzinfo is None:
        raise HTTPException(422, "submitted_at must include a timezone offset")
    existing = db.execute(
        select(Application).where(Application.submission_idempotency_key == body.idempotency_key)
    ).scalar_one_or_none()
    check = check_duplicate(db, company_name=body.company, role_title=body.role_title,
                            external_job_id=body.external_job_id, posting_url=body.posting_url,
                            recruiting_cycle=body.recruiting_cycle,
                            exclude_application_id=existing.id if existing else None)
    if existing is not None:
        return {"application_id": existing.id, "created": False, "duplicate_check": check.as_dict()}

    resume_id = body.resume_version_id
    if resume_id and db.get(ResumeVersion, resume_id) is None:
        raise HTTPException(422, "resume_version_id not found")
    if not resume_id and body.resume_label:
        r = db.execute(select(ResumeVersion).where(ResumeVersion.label == body.resume_label)).scalars().first()
        resume_id = r.id if r else None

    # A tracked-but-unsubmitted record of the same exact opening is upgraded, not duplicated.
    tracked = [m for m in check.matches if m["match_kind"] == "exact_opening" and m["stage"] in {"tracked", "ready"}]
    if len(tracked) == 1:
        app = db.get(Application, tracked[0]["application_id"])
        app.submission_idempotency_key = body.idempotency_key
        app.resume_version_id = resume_id or app.resume_version_id
        mark_submitted(db, app, submitted_at=body.submitted_at, evidence="agent_receipt",
                       actor=f"agent:{body.source}", receipt_id=body.receipt_id)
        db.commit()
        return {"application_id": app.id, "created": False, "duplicate_check": check.as_dict()}
    try:
        app, check = create_application(
            db, company=body.company, role_title=body.role_title, external_job_id=body.external_job_id,
            posting_url=body.posting_url, recruiting_cycle=body.recruiting_cycle, resume_version_id=resume_id,
            application_channel=body.application_channel, stage="submitted", submitted_at=body.submitted_at,
            submission_evidence_type="agent_receipt", submission_receipt_id=body.receipt_id,
            submission_idempotency_key=body.idempotency_key, source="agent", actor=f"agent:{body.source}",
            override_duplicate=body.override_duplicate, override_reason=body.override_reason,
            source_reference=f"agent:{body.source}:{body.receipt_id}",
        )
    except DuplicateBlocked as exc:
        db.rollback()
        raise HTTPException(409, {"message": "Already applied to this exact opening", "duplicate_check": exc.check.as_dict()})
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc))
    db.commit()
    return {"application_id": app.id, "created": True, "duplicate_check": check.as_dict()}
