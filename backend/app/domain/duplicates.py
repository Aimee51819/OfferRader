"""Context-aware duplicate preflight: BLOCK / WARN / ALLOW with reasons.

* BLOCK  - the exact opening (same requisition id or same canonical posting URL)
           in the same or an unspecified recruiting cycle.
* WARN   - same employer with other roles, a same-title opening without an id to
           prove it is different, or a same-id posting in an explicitly
           different cycle without other evidence.
* ALLOW  - nothing comparable at this employer, or a distinct cycle with the
           previous application already closed.
Human override is always possible and is audited by the caller.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Application
from .enums import TERMINAL_STAGES
from .normalize import canonical_url, normalize_req_id, title_similarity
from .resolver import resolve_company

_ORDER = {"ALLOW": 0, "WARN": 1, "BLOCK": 2}


@dataclass
class DuplicateCheck:
    decision: str = "ALLOW"
    reasons: list[str] = field(default_factory=list)
    matches: list[dict] = field(default_factory=list)
    company_id: str | None = None

    def raise_to(self, level: str, reason: str, app: Application | None = None, kind: str = "") -> None:
        if _ORDER[level] > _ORDER[self.decision]:
            self.decision = level
        self.reasons.append(reason)
        if app is not None and all(m["application_id"] != app.id for m in self.matches):
            self.matches.append(
                {
                    "application_id": app.id,
                    "role_title": app.role_title,
                    "external_job_id": app.external_job_id,
                    "posting_url": app.posting_url,
                    "recruiting_cycle": app.recruiting_cycle,
                    "stage": app.stage,
                    "submitted_at": app.submitted_at.isoformat() if app.submitted_at else None,
                    "match_kind": kind,
                    "level": level,
                }
            )

    def as_dict(self) -> dict:
        return {
            "decision": self.decision,
            "reasons": self.reasons,
            "matches": self.matches,
            "company_id": self.company_id,
        }


def _same_cycle(a: str | None, b: str | None) -> bool | None:
    """True/False when both known; None when either side is unknown."""
    if not a or not b:
        return None
    return a.strip().lower() == b.strip().lower()


def check_duplicate(
    db: Session,
    *,
    company_name: str,
    role_title: str | None,
    external_job_id: str | None = None,
    posting_url: str | None = None,
    recruiting_cycle: str | None = None,
    exclude_application_id: str | None = None,
) -> DuplicateCheck:
    result = DuplicateCheck()
    res = resolve_company(db, company_name)
    if res.ambiguous:
        result.raise_to("WARN", f"Company name is ambiguous: {res.reason}.")
        return result
    if res.company is None:
        result.reasons.append("No existing applications at this company.")
        return result
    result.company_id = res.company.id
    apps = db.execute(select(Application).where(Application.company_id == res.company.id)).scalars().all()
    apps = [a for a in apps if a.id != exclude_application_id]
    if not apps:
        result.reasons.append("No existing applications at this company.")
        return result

    req = normalize_req_id(external_job_id)
    url = canonical_url(posting_url)
    for app in apps:
        cycle = _same_cycle(recruiting_cycle, app.recruiting_cycle)
        app_req = normalize_req_id(app.external_job_id)
        exact_id = bool(req and app_req and req == app_req)
        exact_url = bool(url and app.canonical_posting_url and url == app.canonical_posting_url)
        if exact_id or exact_url:
            what = f"requisition {req}" if exact_id else "posting URL"
            if cycle is False:
                if app.stage in TERMINAL_STAGES:
                    result.raise_to(
                        "ALLOW",
                        f"Same {what} as a closed application in a different cycle "
                        f"({app.recruiting_cycle}); treated as a reopened requisition.",
                        app,
                        "reopened_cycle",
                    )
                else:
                    result.raise_to(
                        "WARN",
                        f"Same {what} as an active application in cycle {app.recruiting_cycle}.",
                        app,
                        "same_opening_other_cycle",
                    )
            else:
                result.raise_to(
                    "BLOCK",
                    f"Already applied to this exact opening ({what}, stage {app.stage}).",
                    app,
                    "exact_opening",
                )
            continue
        sim = title_similarity(app.role_title, role_title) if role_title else 0.0
        if sim >= 0.92:
            if req and app_req and req != app_req:
                result.raise_to(
                    "WARN",
                    f"Same title '{app.role_title}' but a different requisition ({app_req}).",
                    app,
                    "same_title_different_req",
                )
            elif cycle is False:
                result.raise_to(
                    "WARN" if app.stage not in TERMINAL_STAGES else "ALLOW",
                    f"Same title applied in a different cycle ({app.recruiting_cycle}).",
                    app,
                    "same_title_other_cycle",
                )
            else:
                result.raise_to(
                    "WARN",
                    f"Likely the same opening: same title '{app.role_title}' with no requisition id "
                    "to tell them apart.",
                    app,
                    "same_title_no_id",
                )
        else:
            result.raise_to(
                "WARN",
                f"You also have a different role here: '{app.role_title}' ({app.stage}).",
                app,
                "other_role_same_company",
            )
    return result
