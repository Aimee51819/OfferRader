"""Applications, postings, resumes, companies, duplicate preflight, action items."""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete as sa_delete
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..domain.duplicates import check_duplicate
from ..domain.normalize import canonical_url, detect_ats, normalize_company, normalize_req_id, normalize_title
from ..domain.resolver import get_or_create_company
from ..models import (
    ActionItem,
    Application,
    ApplicationEvent,
    Assessment,
    Company,
    CompanyAlias,
    JobPosting,
    ResumeVersion,
    utcnow,
)
from ..schemas import (
    ActionItemEdit,
    ActionItemOut,
    AliasIn,
    AliasOut,
    ApplicationCreate,
    ApplicationDetailOut,
    ApplicationOut,
    ApplicationUpdate,
    CompanyOut,
    DuplicateCheckIn,
    DuplicateCheckOut,
    EventOut,
    PostingIn,
    PostingOut,
    ResumeIn,
    ResumeOut,
    StageUpdate,
)
from ..services.applications_service import (
    DuplicateBlocked,
    action_out,
    application_detail,
    application_out,
    create_application,
    mark_submitted,
    set_stage,
)
from ..services.pipeline.apply import audit
from .deps import not_found

router = APIRouter(prefix="/api", tags=["applications"])


@router.get("/applications", response_model=list[ApplicationOut])
def list_applications(
    q: str | None = None,
    company: str | None = None,
    role: str | None = None,
    category: str | None = None,
    stage: list[str] | None = Query(None),
    source: str | None = None,
    resume_version_id: str | None = None,
    submitted_from: date | None = None,
    submitted_to: date | None = None,
    urgency_filter: str | None = Query(None, alias="urgency"),
    needs_review: bool | None = None,
    verified: bool | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(Application).join(Company)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Company.canonical_name.ilike(like),
                Application.role_title.ilike(like),
                Application.external_job_id.ilike(like),
                Application.notes.ilike(like),
            )
        )
    if company:
        stmt = stmt.where(Company.canonical_name.ilike(f"%{company}%"))
    if role:
        stmt = stmt.where(Application.role_title.ilike(f"%{role}%"))
    if category:
        stmt = stmt.where(Application.category == category)
    if stage:
        stmt = stmt.where(Application.stage.in_(stage))
    if resume_version_id:
        stmt = stmt.where(Application.resume_version_id == resume_version_id)
    if submitted_from:
        stmt = stmt.where(Application.submitted_at >= datetime.combine(submitted_from, datetime.min.time(), timezone.utc))
    if submitted_to:
        stmt = stmt.where(Application.submitted_at <= datetime.combine(submitted_to, datetime.max.time(), timezone.utc))
    apps = db.execute(stmt.order_by(Application.updated_at.desc())).scalars().all()
    out = [application_out(db, a) for a in apps]
    if source:
        out = [o for o in out if o["source"] == source]
    if verified is not None:
        out = [o for o in out if o["verified"] == verified]
    if needs_review is not None:
        out = [o for o in out if (o["pending_reviews"] > 0) == needs_review]
    if urgency_filter:
        out = [o for o in out if o["next_action"] and o["next_action"]["urgency"] == urgency_filter]
    return out


@router.post("/applications", response_model=ApplicationOut, status_code=201)
def create(body: ApplicationCreate, db: Session = Depends(get_db)) -> dict:
    submitted_at = body.submitted_at if body.confirmed_submitted else None
    if body.confirmed_submitted and submitted_at is None:
        submitted_at = utcnow()
    stage = body.stage
    if submitted_at and stage in {"tracked", "ready"}:
        stage = "submitted"
    if not body.confirmed_submitted and stage not in {"tracked", "ready"}:
        # A stage past "ready" without confirmed submission would be an unverified assertion.
        raise HTTPException(422, "Set confirmed_submitted=true (you really applied) to use a stage past 'ready'.")
    try:
        app, _check = create_application(
            db,
            company=body.company,
            role_title=body.role_title,
            external_job_id=body.external_job_id,
            posting_url=body.posting_url,
            recruiting_cycle=body.recruiting_cycle,
            category=body.category,
            location=body.location,
            application_channel=body.application_channel,
            resume_version_id=body.resume_version_id,
            stage=stage,
            submitted_at=submitted_at,
            submission_evidence_type="manual_confirmed" if submitted_at else None,
            notes=body.notes,
            override_duplicate=body.override_duplicate,
            override_reason=body.override_reason,
            source="manual",
        )
    except DuplicateBlocked as exc:
        db.rollback()
        raise HTTPException(409, {"message": "Duplicate application blocked", "duplicate_check": exc.check.as_dict()})
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc))
    db.commit()
    return application_out(db, app)


@router.get("/applications/{app_id}", response_model=ApplicationDetailOut)
def get_application(app_id: str, db: Session = Depends(get_db)) -> dict:
    app = db.get(Application, app_id)
    if app is None:
        raise not_found("application not found")
    return application_detail(db, app)


@router.delete("/applications/{app_id}")
def delete_application(app_id: str, db: Session = Depends(get_db)) -> dict:
    """Remove an application and everything attached to it (events, OAs, interviews,
    deadlines cascade in SQLite). Source emails stay in the local store, marked parsed."""
    app = db.get(Application, app_id)
    if app is None:
        raise not_found("application not found")
    from ..services import trash

    trash.snapshot_application(db, app_id, f"{app.company.canonical_name} · {app.role_title}")
    db.expunge(app)
    # Explicit child deletes (children first) so this works even on a connection where
    # SQLite foreign-key cascades are off.
    from ..models import ActionItem, ApplicationEvent, Assessment, Interview, ReviewProposal

    db.execute(sa_delete(ActionItem).where(ActionItem.application_id == app_id))
    db.execute(sa_delete(Interview).where(Interview.application_id == app_id))
    db.execute(sa_delete(Assessment).where(Assessment.application_id == app_id))
    db.execute(
        ReviewProposal.__table__.update()
        .where(ReviewProposal.target_application_id == app_id)
        .values(target_application_id=None)
    )
    db.execute(sa_delete(ApplicationEvent).where(ApplicationEvent.application_id == app_id))
    db.execute(sa_delete(Application).where(Application.id == app_id))
    db.commit()
    return {"deleted": app_id}


@router.patch("/applications/{app_id}", response_model=ApplicationOut)
def update_application(app_id: str, body: ApplicationUpdate, db: Session = Depends(get_db)) -> dict:
    app = db.get(Application, app_id)
    if app is None:
        raise not_found("application not found")
    changes = body.model_dump(exclude_unset=True)
    confirmed = changes.pop("confirmed_submitted", None)
    submitted_at = changes.pop("submitted_at", None)
    if "role_title" in changes:
        app.normalized_role_title = normalize_title(changes["role_title"])
    if "external_job_id" in changes:
        changes["external_job_id"] = normalize_req_id(changes["external_job_id"])
    if "posting_url" in changes:
        app.canonical_posting_url = canonical_url(changes["posting_url"])
        app.ats_platform = detect_ats(changes["posting_url"]) or app.ats_platform
    if changes.get("resume_version_id") and db.get(ResumeVersion, changes["resume_version_id"]) is None:
        raise HTTPException(422, "resume_version_id not found")
    before = {k: getattr(app, k) for k in changes}
    for k, v in changes.items():
        setattr(app, k, v)
    if changes:
        audit(
            db,
            application_id=app.id,
            event_type="manual_edit",
            description="Edited: " + ", ".join(f"{k}: {before[k]!r} -> {v!r}" for k, v in changes.items())[:900],
            key=f"edit:{app.id}:{utcnow().isoformat()}",
        )
    if confirmed:
        mark_submitted(db, app, submitted_at=submitted_at or utcnow(), evidence="manual_confirmed")
    db.commit()
    return application_out(db, app)


@router.post("/applications/{app_id}/stage", response_model=ApplicationOut)
def update_stage(app_id: str, body: StageUpdate, db: Session = Depends(get_db)) -> dict:
    app = db.get(Application, app_id)
    if app is None:
        raise not_found("application not found")
    set_stage(db, app, body.stage, note=body.note, lock=body.lock, event_at=body.event_at)
    db.commit()
    return application_out(db, app)


@router.post("/applications/{app_id}/notes", response_model=ApplicationOut)
def add_note(app_id: str, body: dict, db: Session = Depends(get_db)) -> dict:
    app = db.get(Application, app_id)
    if app is None:
        raise not_found("application not found")
    text = str(body.get("note") or "").strip()[:2000]
    if not text:
        raise HTTPException(422, "note is required")
    app.notes = ((app.notes + "\n\n") if app.notes else "") + text
    audit(db, application_id=app.id, event_type="note", description=text, key=f"note:{app.id}:{utcnow().isoformat()}")
    db.commit()
    return application_out(db, app)


@router.get("/applications/{app_id}/events", response_model=list[EventOut])
def application_events(app_id: str, db: Session = Depends(get_db)) -> list:
    return db.execute(
        select(ApplicationEvent).where(ApplicationEvent.application_id == app_id).order_by(ApplicationEvent.event_at.desc())
    ).scalars().all()


@router.get("/events", response_model=list[EventOut])
def recent_events(since: datetime | None = None, limit: int = Query(100, le=500), db: Session = Depends(get_db)) -> list:
    stmt = select(ApplicationEvent).order_by(ApplicationEvent.recorded_at.desc()).limit(limit)
    if since:
        stmt = stmt.where(ApplicationEvent.recorded_at >= since)
    return db.execute(stmt).scalars().all()


# --- duplicates -------------------------------------------------------------------------


@router.post("/duplicates/check", response_model=DuplicateCheckOut)
def duplicate_check(body: DuplicateCheckIn, db: Session = Depends(get_db)) -> dict:
    return check_duplicate(
        db,
        company_name=body.company,
        role_title=body.role_title,
        external_job_id=body.external_job_id,
        posting_url=body.posting_url,
        recruiting_cycle=body.recruiting_cycle,
    ).as_dict()


# --- companies / aliases ------------------------------------------------------------------


@router.get("/companies", response_model=list[CompanyOut])
def companies(db: Session = Depends(get_db)) -> list:
    return db.execute(select(Company).order_by(Company.canonical_name)).scalars().all()


@router.get("/companies/{company_id}/aliases", response_model=list[AliasOut])
def aliases(company_id: str, db: Session = Depends(get_db)) -> list:
    return db.execute(select(CompanyAlias).where(CompanyAlias.company_id == company_id)).scalars().all()


@router.post("/companies/{company_id}/aliases", response_model=AliasOut, status_code=201)
def add_alias(company_id: str, body: AliasIn, db: Session = Depends(get_db)) -> CompanyAlias:
    if db.get(Company, company_id) is None:
        raise not_found("company not found")
    norm = body.alias.strip().lower() if body.alias_type == "domain" else normalize_company(body.alias)
    if body.alias_type == "domain":
        from ..domain.normalize import is_shared_sender_domain

        if is_shared_sender_domain(norm):
            raise HTTPException(422, "That domain is shared by many employers (ATS/job board) and can't be an alias.")
    row = CompanyAlias(company_id=company_id, alias=body.alias.strip(), normalized_alias=norm,
                       alias_type=body.alias_type, verified=body.verified)
    db.add(row)
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        raise HTTPException(409, "alias already exists")
    return row


# --- postings / resumes -------------------------------------------------------------------


@router.get("/postings", response_model=list[PostingOut])
def postings(db: Session = Depends(get_db)) -> list:
    return db.execute(select(JobPosting).order_by(JobPosting.first_seen_at.desc())).scalars().all()


@router.post("/postings", response_model=PostingOut, status_code=201)
def add_posting(body: PostingIn, db: Session = Depends(get_db)) -> JobPosting:
    comp = get_or_create_company(db, body.company)
    row = JobPosting(
        company_id=comp.id, title=body.title, normalized_title=normalize_title(body.title),
        external_job_id=normalize_req_id(body.external_job_id), posting_url=body.posting_url,
        canonical_posting_url=canonical_url(body.posting_url), ats_platform=detect_ats(body.posting_url),
        location=body.location, work_mode=body.work_mode, employment_type=body.employment_type,
        season=body.season, source="manual", description_excerpt=body.description_excerpt,
    )
    db.add(row)
    db.commit()
    return row


@router.get("/resumes", response_model=list[ResumeOut])
def resumes(db: Session = Depends(get_db)) -> list:
    return db.execute(select(ResumeVersion).order_by(ResumeVersion.created_at.desc())).scalars().all()


@router.post("/resumes", response_model=ResumeOut, status_code=201)
def add_resume(body: ResumeIn, db: Session = Depends(get_db)) -> ResumeVersion:
    # Metadata only. Resume bytes are never read, stored, or sent anywhere.
    row = ResumeVersion(**body.model_dump())
    db.add(row)
    db.commit()
    return row


# --- action items / assessments -------------------------------------------------------------


@router.get("/action-items", response_model=list[ActionItemOut])
def action_items(
    state: list[str] | None = Query(None),
    urgency_filter: str | None = Query(None, alias="urgency"),
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(ActionItem)
    stmt = stmt.where(ActionItem.state.in_(state or ["open", "needs_review"]))
    rows = [action_out(i, db=db) for i in db.execute(stmt).scalars()]
    if urgency_filter:
        rows = [r for r in rows if r["urgency"] == urgency_filter]
    return sorted(rows, key=_due_sort_key)


def _due_sort_key(r: dict) -> tuple:
    if r["due_at"]:
        return (0, r["due_at"].isoformat())
    if r["due_date"]:
        return (0, r["due_date"] + "T00:00")
    return (1, "")


@router.delete("/action-items/{item_id}")
def delete_action_item(item_id: str, db: Session = Depends(get_db)) -> dict:
    """Delete a deadline record (used for the Done list). The application is untouched."""
    from ..services import trash

    item = db.get(ActionItem, item_id)
    if item is None:
        raise not_found("action item not found")
    app = db.get(Application, item.application_id)
    trash.snapshot_deadline(db, item_id, f"{app.company.canonical_name if app else '?'} · {item.title}")
    db.delete(item)
    db.commit()
    return {"deleted": item_id}


@router.get("/trash/{kind}")
def trash_peek(kind: str, db: Session = Depends(get_db)) -> dict:
    """The most recent removal that can be recovered (or null)."""
    from ..services import trash

    if kind not in trash.KINDS:
        raise not_found("unknown kind")
    return {"last": trash.peek(db, kind)}


@router.post("/trash/{kind}/restore")
def trash_restore(kind: str, db: Session = Depends(get_db)) -> dict:
    """Undo the most recent removal of this kind."""
    from ..services import trash

    try:
        out = trash.restore_last(db, kind)
    except trash.TrashError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return out


@router.post("/action-items/{item_id}", response_model=ActionItemOut)
def edit_action_item(item_id: str, body: ActionItemEdit, db: Session = Depends(get_db)) -> dict:
    item = db.get(ActionItem, item_id)
    if item is None:
        raise not_found("action item not found")
    changes = body.model_dump(exclude_unset=True)
    desc = []
    if "due_at" in changes or "due_date" in changes:
        if changes.get("due_at") is not None and changes["due_at"].tzinfo is None:
            raise HTTPException(422, "due_at must include a timezone offset")
        desc.append(f"deadline {item.due_at or item.due_date} -> {changes.get('due_at') or changes.get('due_date')}")
        item.due_at = changes.get("due_at")
        item.due_date = changes.get("due_date")
        item.due_precision = "instant" if item.due_at else ("day" if item.due_date else "unknown")
        item.deadline_assumption = "Set manually by the user."
        item.deadline_confidence = 1.0
        if item.state == "needs_review":
            item.state = "open"
    if changes.get("state"):
        desc.append(f"state {item.state} -> {changes['state']}")
        item.state = changes["state"]
        if item.state == "completed":
            item.completed_at = utcnow()
            if item.assessment_id:
                a = db.get(Assessment, item.assessment_id)
                if a and a.state in {"invited", "started"}:
                    a.state, a.completed_at = "completed", utcnow()
    if changes.get("note"):
        item.instruction = ((item.instruction or "") + f" [Note: {changes['note']}]").strip()
    audit(db, application_id=item.application_id, event_type="action_item_edit",
          description=f"{item.title}: " + "; ".join(desc or ["note added"]), key=f"ai:{item.id}:{utcnow().isoformat()}")
    db.commit()
    return action_out(item, db=db)


@router.post("/assessments/{assessment_id}/complete", response_model=ApplicationOut)
def complete_assessment(assessment_id: str, db: Session = Depends(get_db)) -> dict:
    a = db.get(Assessment, assessment_id)
    if a is None:
        raise not_found("assessment not found")
    a.state, a.completed_at = "completed", utcnow()
    for item in db.execute(select(ActionItem).where(ActionItem.assessment_id == a.id, ActionItem.state.in_(("open", "needs_review")))).scalars():
        item.state, item.completed_at = "completed", utcnow()
    app = db.get(Application, a.application_id)
    prev = app.stage
    if app.stage == "assessment_invited":
        app.stage, app.stage_updated_at = "assessment_completed", utcnow()
        app.stage_evidence_at = utcnow()
    audit(db, application_id=app.id, event_type="assessment_completed", description="Marked assessment completed manually.",
          from_stage=prev if app.stage != prev else None, to_stage=app.stage if app.stage != prev else None,
          key=f"oa-done:{a.id}:{utcnow().isoformat()}")
    db.commit()
    return application_out(db, app)

