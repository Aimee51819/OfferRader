"""CSV import (with duplicate preflight), CSV/JSON export, ICS export."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..domain.duplicates import check_duplicate
from ..domain.enums import CATEGORIES, CHANNELS, STAGES
from ..models import ActionItem, Application, ApplicationEvent, Interview, ResumeVersion, utcnow
from ..services.applications_service import DuplicateBlocked, application_out, create_application

router = APIRouter(prefix="/api", tags=["import-export"])

CSV_FIELDS = [
    "company", "role_title", "external_job_id", "posting_url", "recruiting_cycle", "category", "location",
    "application_channel", "stage", "submitted_at", "submission_evidence_type", "resume_label", "notes",
]
_MAX_IMPORT_BYTES = 2_000_000


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(v, "%m/%d/%Y")
        except ValueError as exc:
            raise ValueError(f"unrecognized date '{v}' (use ISO 8601)") from exc
    if dt.tzinfo is None:
        # A date without a time zone: keep the calendar day, anchored at 12:00 UTC so it
        # displays as the same day in US time zones. Recorded as an assumption in notes.
        dt = dt.replace(hour=12, tzinfo=timezone.utc)
    return dt


@router.post("/import/csv")
async def import_csv(
    file: UploadFile = File(...),
    dry_run: bool = Form(True),
    override_rows: str = Form(""),
    db: Session = Depends(get_db),
) -> dict:
    raw = await file.read(_MAX_IMPORT_BYTES + 1)
    if len(raw) > _MAX_IMPORT_BYTES:
        raise HTTPException(413, "CSV too large (2 MB max)")
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    overrides = {int(x) for x in override_rows.split(",") if x.strip().isdigit()}
    resumes = {r.label.lower(): r.id for r in db.execute(select(ResumeVersion)).scalars()}
    results = []
    created = 0
    for i, row in enumerate(reader, start=1):
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        res: dict = {"row": i, "company": row.get("company"), "role_title": row.get("role_title") or row.get("title")}
        try:
            company = row.get("company")
            title = row.get("role_title") or row.get("title")
            if not company or not title:
                raise ValueError("company and role_title are required")
            stage = (row.get("stage") or "tracked").lower()
            if stage not in STAGES:
                raise ValueError(f"unknown stage '{stage}'")
            category = row.get("category") or None
            if category and category not in CATEGORIES:
                raise ValueError(f"unknown category '{category}'")
            channel = row.get("application_channel") or None
            if channel and channel not in CHANNELS:
                raise ValueError(f"unknown application_channel '{channel}'")
            submitted = _parse_dt(row.get("submitted_at"))
            if stage not in {"tracked", "ready"} and submitted is None:
                raise ValueError("rows past 'ready' need submitted_at (your own record of applying)")
            check = check_duplicate(db, company_name=company, role_title=title,
                                    external_job_id=row.get("external_job_id"), posting_url=row.get("posting_url"),
                                    recruiting_cycle=row.get("recruiting_cycle"))
            res["duplicate_check"] = check.as_dict()
            probable = any(m["match_kind"] == "same_title_no_id" for m in check.matches)
            if probable and i not in overrides:
                res["action"] = "would_skip" if dry_run else "skipped_probable_duplicate"
                results.append(res)
                continue
            if dry_run:
                res["action"] = "would_skip" if check.decision == "BLOCK" and i not in overrides else "would_import"
                results.append(res)
                continue
            with db.begin_nested():
                app, _ = create_application(
                    db, company=company, role_title=title, external_job_id=row.get("external_job_id") or None,
                    posting_url=row.get("posting_url") or None, recruiting_cycle=row.get("recruiting_cycle") or None,
                    category=category, location=row.get("location") or None, application_channel=channel,
                    resume_version_id=resumes.get((row.get("resume_label") or "").lower()), stage=stage,
                    submitted_at=submitted, submission_evidence_type="manual_confirmed" if submitted else None,
                    notes=row.get("notes") or None, source="import", actor="user:csv_import",
                    override_duplicate=i in overrides, override_reason="CSV import override" if i in overrides else None,
                    source_reference=f"import:{file.filename}:{i}",
                )
            res.update({"action": "imported", "application_id": app.id})
            created += 1
        except DuplicateBlocked as exc:
            res.update({"action": "skipped_duplicate", "duplicate_check": exc.check.as_dict()})
        except ValueError as exc:
            res.update({"action": "error", "error": str(exc)})
        results.append(res)
    if not dry_run:
        db.commit()
    return {"dry_run": dry_run, "created": created, "rows": results}


@router.get("/export/applications.csv")
def export_csv(db: Session = Depends(get_db)) -> Response:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS + ["id", "verified", "acknowledged_at", "ats_platform"])
    w.writeheader()
    for a in db.execute(select(Application)).scalars():
        w.writerow({
            "id": a.id, "company": a.company.canonical_name, "role_title": a.role_title,
            "external_job_id": a.external_job_id or "", "posting_url": a.posting_url or "",
            "recruiting_cycle": a.recruiting_cycle or "", "category": a.category or "", "location": a.location or "",
            "application_channel": a.application_channel or "", "stage": a.stage,
            "submitted_at": a.submitted_at.isoformat() if a.submitted_at else "",
            "submission_evidence_type": a.submission_evidence_type or "",
            "resume_label": a.resume_version.label if a.resume_version else "", "notes": a.notes or "",
            "verified": a.is_verified_submission, "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else "",
            "ats_platform": a.ats_platform or "",
        })
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="applications.csv"'})


@router.get("/export/json")
def export_json(db: Session = Depends(get_db)) -> Response:
    def row(obj) -> dict:
        return {c.name: (getattr(obj, c.name).isoformat() if isinstance(getattr(obj, c.name), datetime) else getattr(obj, c.name))
                for c in obj.__table__.columns}

    apps = []
    for a in db.execute(select(Application)).scalars():
        d = application_out(db, a)
        d = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in d.items() if k != "next_action"}
        d["events"] = [row(e) for e in db.execute(select(ApplicationEvent).where(ApplicationEvent.application_id == a.id)).scalars()]
        d["action_items"] = [row(i) for i in db.execute(select(ActionItem).where(ActionItem.application_id == a.id)).scalars()]
        apps.append(d)
    body = json.dumps({"exported_at": utcnow().isoformat(), "schema": "apptracker-export/1", "applications": apps}, indent=1, default=str)
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="applications.json"'})


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _ics_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@router.get("/export/deadlines.ics")
def export_ics(db: Session = Depends(get_db)) -> Response:
    """Calendar file for the user to import manually. Nothing is written to any calendar."""
    now = _ics_dt(utcnow())
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//AppTracker//Deadlines//EN", "CALSCALE:GREGORIAN"]
    items = db.execute(select(ActionItem).where(ActionItem.state.in_(("open", "needs_review")))).scalars().all()
    for i in items:
        if i.due_at is None and not i.due_date:
            continue
        app = i.application
        summary = f"{i.title} — {app.company.canonical_name}: {app.role_title}"
        desc = f"Deadline as written: {i.due_expression_raw or 'n/a'}"
        if i.deadline_assumption:
            desc += f"\nAssumptions: {i.deadline_assumption}"
        lines += ["BEGIN:VEVENT", f"UID:{i.id}@apptracker.local", f"DTSTAMP:{now}"]
        if i.due_at is not None:
            lines += [f"DTSTART:{_ics_dt(i.due_at)}", f"DTEND:{_ics_dt(i.due_at)}"]
        else:
            lines += [f"DTSTART;VALUE=DATE:{i.due_date.replace('-', '')}"]
            desc += "\nDate only: no time of day was stated."
        lines += [f"SUMMARY:{_ics_escape(summary)}", f"DESCRIPTION:{_ics_escape(desc)}",
                  "BEGIN:VALARM", "TRIGGER:-PT24H", "ACTION:DISPLAY", f"DESCRIPTION:{_ics_escape(summary)}", "END:VALARM",
                  "END:VEVENT"]
    _ = Interview
    lines.append("END:VCALENDAR")
    return Response("\r\n".join(lines) + "\r\n", media_type="text/calendar",
                    headers={"Content-Disposition": 'attachment; filename="apptracker-deadlines.ics"'})
