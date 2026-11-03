from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Application,
    ApplicationStatus,
    DismissedDuplicate,
    EventSource,
    ProcessedEmail,
    StatusEvent,
    Suggestion,
)
from ..schemas import (
    ApplicationCreate,
    ApplicationDetailOut,
    ApplicationOut,
    ApplicationUpdate,
    DismissedDuplicateOut,
    DuplicateGroupOut,
    DuplicatePairIn,
    EmailActivityOut,
    MergeApplicationsIn,
    MessageOut,
    StatusEventOut,
)
from ..services.settings_service import get_follow_up_days
from ..services.suggestion_service import is_placeholder_title, normalize_label

router = APIRouter(prefix="/api/applications", tags=["applications"])

EXPORT_FIELDS = [
    "company",
    "title",
    "location",
    "url",
    "source",
    "salary",
    "status",
    "skills",
    "notes",
    "contact_email",
    "date_applied",
    "description",
]


@router.get("", response_model=list[ApplicationOut])
def list_applications(
    status: ApplicationStatus | None = None,
    search: str | None = Query(None),
    db: Session = Depends(get_db),
) -> list[Application]:
    stmt = select(Application)
    if status is not None:
        stmt = stmt.where(Application.status == status)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            or_(
                Application.company.ilike(like),
                Application.title.ilike(like),
                Application.location.ilike(like),
            )
        )
    stmt = stmt.order_by(Application.updated_at.desc())
    return list(db.execute(stmt).scalars().all())


@router.post("", response_model=ApplicationOut, status_code=201)
def create_application(
    payload: ApplicationCreate, db: Session = Depends(get_db)
) -> Application:
    app = Application(**payload.model_dump())
    db.add(app)
    db.flush()
    db.add(
        StatusEvent(
            application_id=app.id,
            from_status=None,
            to_status=app.status,
            note="Application created",
            source=EventSource.manual,
        )
    )
    db.commit()
    db.refresh(app)
    return app


@router.get("/export")
def export_applications(
    format: str = Query("csv", pattern="^(csv|json)$"),
    db: Session = Depends(get_db),
) -> Response:
    apps = db.execute(
        select(Application).order_by(Application.created_at.asc())
    ).scalars().all()

    def _value(app: Application, field: str) -> str:
        val = getattr(app, field)
        if isinstance(val, ApplicationStatus):
            return val.value
        if isinstance(val, datetime):
            return val.isoformat()
        return "" if val is None else str(val)

    stamp = datetime.now().strftime("%Y%m%d")
    if format == "json":
        payload = [{f: _value(a, f) for f in EXPORT_FIELDS} for a in apps]
        return Response(
            content=json.dumps(payload, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": f"attachment; filename=applications-{stamp}.json"
            },
        )

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_FIELDS)
    writer.writeheader()
    for a in apps:
        writer.writerow({f: _value(a, f) for f in EXPORT_FIELDS})
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=applications-{stamp}.csv"
        },
    )


def _coerce_status(value: str | None) -> ApplicationStatus:
    if not value:
        return ApplicationStatus.saved
    try:
        return ApplicationStatus(value.strip().lower())
    except ValueError:
        return ApplicationStatus.saved


def _coerce_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for parse in (datetime.fromisoformat,):
        try:
            return parse(value)
        except ValueError:
            continue
    return None


_FOLLOW_UP_STATUSES = {
    ApplicationStatus.applied,
    ApplicationStatus.online_assessment,
    ApplicationStatus.phone_screen,
    ApplicationStatus.interview,
}


def _normalize_company(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


# Too generic to safely substring-match against all inbox subjects.
_GENERIC_COMPANY_MATCH = {
    "entry",
    "level",
    "software",
    "engineer",
    "intern",
    "new",
    "grad",
    "team",
    "careers",
    "jobs",
    "hr",
    "talent",
}


def _related_emails(db: Session, app: Application) -> list[EmailActivityOut]:
    items: list[EmailActivityOut] = []
    suggestions = db.execute(
        select(Suggestion)
        .where(Suggestion.application_id == app.id)
        .order_by(Suggestion.created_at.desc())
    ).scalars().all()
    for s in suggestions:
        items.append(
            EmailActivityOut(
                id=s.id,
                kind="suggestion",
                subject=s.email_subject,
                sender=s.email_sender,
                snippet=s.email_snippet,
                summary=s.summary,
                suggestion_status=s.status,
                is_job_related=True,
                created_at=s.created_at,
            )
        )

    # Fuzzy inbox match only for distinctive company names (never "Entry").
    norm = _normalize_company(app.company or "")
    if norm and len(norm) >= 5 and norm not in _GENERIC_COMPANY_MATCH:
        recent = db.execute(
            select(ProcessedEmail)
            .where(ProcessedEmail.is_job_related.is_(True))
            .order_by(ProcessedEmail.created_at.desc())
            .limit(200)
        ).scalars().all()
        seen_subjects = {
            (i.subject or "").strip().lower() for i in items if i.subject
        }
        # Prefer whole-token match so "entry" doesn't hit "entry-level" spam.
        token = re.compile(rf"\b{re.escape(norm)}\b", re.IGNORECASE)
        for pe in recent:
            blob = f"{pe.subject or ''} {pe.sender or ''}"
            if not token.search(_normalize_company(blob)) and not token.search(blob):
                continue
            key = (pe.subject or "").strip().lower()
            if key and key in seen_subjects:
                continue
            items.append(
                EmailActivityOut(
                    id=pe.id,
                    kind="processed_email",
                    subject=pe.subject,
                    sender=pe.sender,
                    snippet=None,
                    summary=None,
                    suggestion_status=None,
                    is_job_related=pe.is_job_related,
                    created_at=pe.created_at,
                )
            )
            if key:
                seen_subjects.add(key)

    items.sort(key=lambda x: x.created_at, reverse=True)
    return items[:40]


def _detail_out(db: Session, app: Application) -> ApplicationDetailOut:
    return ApplicationDetailOut(
        id=app.id,
        company=app.company,
        title=app.title,
        location=app.location,
        url=app.url,
        source=app.source,
        salary=app.salary,
        status=app.status,
        description=app.description,
        skills=app.skills,
        notes=app.notes,
        contact_email=app.contact_email,
        job_id=app.job_id,
        date_applied=app.date_applied,
        created_at=app.created_at,
        updated_at=app.updated_at,
        events=[StatusEventOut.model_validate(e) for e in (app.events or [])],
        related_emails=_related_emails(db, app),
    )


@router.get("/reminders", response_model=list[ApplicationOut])
def list_follow_up_reminders(db: Session = Depends(get_db)) -> list[Application]:
    """Applications awaiting a reply longer than the configured follow-up window."""
    days = get_follow_up_days(db)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    apps = db.execute(
        select(Application)
        .where(Application.status.in_(_FOLLOW_UP_STATUSES))
        .order_by(Application.updated_at.asc())
    ).scalars().all()
    due: list[Application] = []
    for app in apps:
        updated = app.updated_at
        if updated is None:
            continue
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if updated <= cutoff:
            due.append(app)
    return due


def _title_overlap(a: str, b: str) -> float:
    """Jaccard overlap of title words, after stripping boilerplate."""
    ta = {w for w in normalize_label(a).split() if len(w) > 1}
    tb = {w for w in normalize_label(b).split() if len(w) > 1}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@router.get("/duplicates", response_model=list[DuplicateGroupOut])
def list_duplicate_candidates(db: Session = Depends(get_db)) -> list[DuplicateGroupOut]:
    """Applications that look like one posting recorded twice.

    Deliberately conservative: several genuinely different reqs at the same
    employer are normal, so rows whose job ids disagree are never paired, and
    titles must overlap heavily to be suggested.
    """
    apps = list(db.execute(select(Application)).scalars().all())
    dismissed = {
        (low, high)
        for low, high in db.execute(
            select(DismissedDuplicate.low_id, DismissedDuplicate.high_id)
        ).all()
    }
    by_company: dict[str, list[Application]] = {}
    for app in apps:
        key = normalize_label(app.company)
        if key:
            by_company.setdefault(key, []).append(app)

    groups: list[DuplicateGroupOut] = []
    for company_apps in by_company.values():
        if len(company_apps) < 2:
            continue
        for i, first in enumerate(company_apps):
            for second in company_apps[i + 1 :]:
                if _pair_key(first.id, second.id) in dismissed:
                    continue  # user said these are genuinely different
                jid_a = (first.job_id or "").strip()
                jid_b = (second.job_id or "").strip()
                if jid_a and jid_b and jid_a != jid_b:
                    continue  # different requisitions at the same employer
                if jid_a and jid_b and jid_a == jid_b:
                    reason = f"Same job id {jid_a}"
                elif is_placeholder_title(first.title) or is_placeholder_title(
                    second.title
                ):
                    reason = "One row has no role title yet"
                elif _title_overlap(first.title, second.title) >= 0.7:
                    reason = "Nearly identical role titles"
                else:
                    continue
                groups.append(
                    DuplicateGroupOut(
                        company=first.company,
                        reason=reason,
                        applications=[
                            ApplicationOut.model_validate(first),
                            ApplicationOut.model_validate(second),
                        ],
                    )
                )
    return groups


def _pair_key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _forget_dismissals(db: Session, app_id: int) -> None:
    """Drop dismissals naming an application that is about to disappear.

    SQLite reuses the highest rowid after a delete, so a stale row could
    otherwise silently hide a future, unrelated pair.
    """
    db.execute(
        delete(DismissedDuplicate).where(
            or_(DismissedDuplicate.low_id == app_id, DismissedDuplicate.high_id == app_id)
        )
    )


@router.get("/duplicates/dismissed", response_model=list[DismissedDuplicateOut])
def list_dismissed_duplicates(db: Session = Depends(get_db)) -> list[DismissedDuplicateOut]:
    rows = db.execute(
        select(DismissedDuplicate).order_by(
            DismissedDuplicate.created_at.desc(), DismissedDuplicate.id.desc()
        )
    ).scalars().all()
    ids = {i for r in rows for i in (r.low_id, r.high_id)}
    apps = {
        a.id: a
        for a in db.execute(select(Application).where(Application.id.in_(ids))).scalars()
    }
    out: list[DismissedDuplicateOut] = []
    for r in rows:
        low, high = apps.get(r.low_id), apps.get(r.high_id)
        if low is None or high is None:
            continue  # orphaned row; never surface a pair we can't name
        out.append(
            DismissedDuplicateOut(
                low_id=r.low_id,
                high_id=r.high_id,
                created_at=r.created_at,
                applications=[
                    ApplicationOut.model_validate(low),
                    ApplicationOut.model_validate(high),
                ],
            )
        )
    return out


@router.post("/duplicates/dismiss", response_model=DismissedDuplicateOut)
def dismiss_duplicate(
    payload: DuplicatePairIn, db: Session = Depends(get_db)
) -> DismissedDuplicate:
    """Stop suggesting this pair. Idempotent and order-independent."""
    if payload.first_id == payload.second_id:
        raise HTTPException(status_code=400, detail="A pair needs two different applications")
    low, high = _pair_key(payload.first_id, payload.second_id)
    if db.get(Application, low) is None or db.get(Application, high) is None:
        raise HTTPException(status_code=404, detail="Application not found")

    stmt = select(DismissedDuplicate).where(
        DismissedDuplicate.low_id == low, DismissedDuplicate.high_id == high
    )
    existing = db.execute(stmt).scalar_one_or_none()
    if existing is not None:
        return existing
    row = DismissedDuplicate(low_id=low, high_id=high)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent request inserted the same pair first.
        db.rollback()
        return db.execute(stmt).scalar_one()
    db.refresh(row)
    return row


@router.delete("/duplicates/dismissed/{first_id}/{second_id}", response_model=MessageOut)
def restore_duplicate(
    first_id: int, second_id: int, db: Session = Depends(get_db)
) -> MessageOut:
    """Undo a dismissal so the pair can be suggested again. Idempotent."""
    low, high = _pair_key(first_id, second_id)
    db.execute(
        delete(DismissedDuplicate).where(
            DismissedDuplicate.low_id == low, DismissedDuplicate.high_id == high
        )
    )
    db.commit()
    return MessageOut(message="Duplicate suggestion restored")


@router.post("/merge", response_model=ApplicationDetailOut)
def merge_applications(
    payload: MergeApplicationsIn, db: Session = Depends(get_db)
) -> ApplicationDetailOut:
    """Merge source into target: move events/suggestions, enrich fields, delete source."""
    if payload.source_id == payload.target_id:
        raise HTTPException(status_code=400, detail="Cannot merge an application into itself")
    source = db.get(Application, payload.source_id)
    target = db.get(Application, payload.target_id)
    if source is None or target is None:
        raise HTTPException(status_code=404, detail="Application not found")

    for event in list(source.events):
        event.application_id = target.id
    for suggestion in list(source.suggestions):
        suggestion.application_id = target.id

    # Prefer non-placeholder title/company and fill empty optional fields.
    if (not target.title or target.title.lower() == "unknown") and source.title:
        target.title = source.title
    if source.job_id and not target.job_id:
        target.job_id = source.job_id
    # "Ghosted" records an absence of news, so any real outcome on the other
    # row outranks it regardless of which way round the merge was done.
    if target.status == ApplicationStatus.ghosted and source.status != ApplicationStatus.ghosted:
        target.status = source.status
    if source.location and not target.location:
        target.location = source.location
    if source.url and not target.url:
        target.url = source.url
    if source.salary and not target.salary:
        target.salary = source.salary
    if source.notes:
        target.notes = (
            f"{target.notes}\n\n--- merged ---\n{source.notes}".strip()
            if target.notes
            else source.notes
        )
    if source.description and not target.description:
        target.description = source.description
    if source.skills and not target.skills:
        target.skills = source.skills
    if source.contact_email and not target.contact_email:
        target.contact_email = source.contact_email
    if source.date_applied and (
        not target.date_applied or source.date_applied < target.date_applied
    ):
        target.date_applied = source.date_applied

    db.add(
        StatusEvent(
            application_id=target.id,
            from_status=target.status,
            to_status=target.status,
            note=f"Merged application #{source.id} ({source.company} — {source.title})",
            source=EventSource.system,
        )
    )
    _forget_dismissals(db, source.id)
    db.delete(source)
    db.commit()
    db.refresh(target)
    return _detail_out(db, target)


@router.post("/import", response_model=MessageOut)
async def import_applications(
    file: UploadFile = File(...), db: Session = Depends(get_db)
) -> MessageOut:
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    filename = (file.filename or "").lower()

    rows: list[dict] = []
    if filename.endswith(".json") or raw.lstrip().startswith(("[", "{")):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc
        rows = data if isinstance(data, list) else [data]
    else:
        rows = list(csv.DictReader(io.StringIO(raw)))

    imported = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        company = (row.get("company") or "").strip()
        title = (row.get("title") or "").strip()
        if not company and not title:
            continue
        app = Application(
            company=company or "Unknown",
            title=title or "Unknown",
            location=(row.get("location") or None),
            url=(row.get("url") or None),
            source=(row.get("source") or None),
            salary=(row.get("salary") or None),
            status=_coerce_status(row.get("status")),
            skills=(row.get("skills") or None),
            notes=(row.get("notes") or None),
            contact_email=(row.get("contact_email") or None),
            date_applied=_coerce_date(row.get("date_applied")),
            description=(row.get("description") or None),
        )
        db.add(app)
        db.flush()
        db.add(
            StatusEvent(
                application_id=app.id,
                from_status=None,
                to_status=app.status,
                note="Imported",
                source=EventSource.system,
            )
        )
        imported += 1

    db.commit()
    return MessageOut(message=f"Imported {imported} application(s).")


@router.get("/{app_id}", response_model=ApplicationDetailOut)
def get_application(
    app_id: int, db: Session = Depends(get_db)
) -> ApplicationDetailOut:
    app = db.get(Application, app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return _detail_out(db, app)


@router.patch("/{app_id}", response_model=ApplicationDetailOut)
def update_application(
    app_id: int, payload: ApplicationUpdate, db: Session = Depends(get_db)
) -> ApplicationDetailOut:
    app = db.get(Application, app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")

    data = payload.model_dump(exclude_unset=True)
    status_note = data.pop("status_note", None)
    new_status = data.get("status")

    if new_status is not None and new_status != app.status:
        db.add(
            StatusEvent(
                application_id=app.id,
                from_status=app.status,
                to_status=new_status,
                note=status_note or "Status updated",
                source=EventSource.manual,
            )
        )

    for key, value in data.items():
        setattr(app, key, value)

    db.commit()
    db.refresh(app)
    return _detail_out(db, app)


@router.delete("/{app_id}", response_model=MessageOut)
def delete_application(app_id: int, db: Session = Depends(get_db)) -> MessageOut:
    app = db.get(Application, app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    _forget_dismissals(db, app.id)
    db.delete(app)
    db.commit()
    return MessageOut(message="Application deleted")
