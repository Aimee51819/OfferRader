"""Entity resolution: which company, and which application, an email is about.

Principles
* A company is never inferred from a shared ATS/assessment/job-board domain.
* An alias that maps to more than one company is ambiguous and never auto-resolves.
* A company with several applications needs role or requisition evidence for an
  automatic match. Company-only matches always go to review.
* One event is attached to at most one application (no fan-out).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Application, ApplicationEvent, Company, CompanyAlias, EmailMessage
from .normalize import (
    canonical_url,
    is_placeholder_title,
    is_shared_sender_domain,
    normalize_company,
    normalize_req_id,
    title_similarity,
)

AUTO_MATCH_MIN_SCORE = 0.85
AUTO_MATCH_MIN_MARGIN = 0.25


@dataclass
class CompanyResolution:
    company: Company | None
    confidence: float
    reason: str
    ambiguous: bool = False
    candidates: list[dict] = field(default_factory=list)


def resolve_company(
    db: Session,
    name: str | None,
    *,
    sender_domain: str | None = None,
    company_domain: str | None = None,
) -> CompanyResolution:
    norm = normalize_company(name)
    if norm:
        exact = db.execute(select(Company).where(Company.normalized_name == norm)).scalar_one_or_none()
        if exact is not None:
            return CompanyResolution(exact, 0.95, f"exact company name '{exact.canonical_name}'")
        alias_rows = db.execute(
            select(CompanyAlias).where(
                CompanyAlias.normalized_alias == norm, CompanyAlias.alias_type.in_(("name", "manual"))
            )
        ).scalars().all()
        ids = {a.company_id for a in alias_rows}
        if len(ids) == 1:
            alias = alias_rows[0]
            company = db.get(Company, alias.company_id)
            conf = 0.9 if alias.verified else 0.7
            return CompanyResolution(company, conf, f"alias '{alias.alias}' ({'verified' if alias.verified else 'unverified'})")
        if len(ids) > 1:
            cands = [
                {"company_id": cid, "name": db.get(Company, cid).canonical_name} for cid in ids
            ]
            return CompanyResolution(None, 0.0, f"alias '{name}' matches {len(ids)} companies", True, cands)

    for dom in (company_domain, sender_domain):
        if not dom or is_shared_sender_domain(dom):
            continue
        dom = dom.lower()
        rows = db.execute(
            select(CompanyAlias).where(
                CompanyAlias.normalized_alias == dom,
                CompanyAlias.alias_type == "domain",
                CompanyAlias.verified.is_(True),
            )
        ).scalars().all()
        ids = {r.company_id for r in rows}
        if len(ids) == 1:
            company = db.get(Company, rows[0].company_id)
            # Domain alone is weaker than a name: never above review threshold for sensitive moves.
            return CompanyResolution(company, 0.75, f"verified domain alias '{dom}'")
        by_site = db.execute(select(Company).where(Company.website_domain == dom)).scalars().all()
        if len(by_site) == 1:
            return CompanyResolution(by_site[0], 0.7, f"company website domain '{dom}'")
    return CompanyResolution(None, 0.0, "no known company matches")


def get_or_create_company(db: Session, name: str, *, domain: str | None = None) -> Company:
    norm = normalize_company(name) or name.strip().lower()
    company = db.execute(select(Company).where(Company.normalized_name == norm)).scalar_one_or_none()
    if company is None:
        company = Company(canonical_name=name.strip(), normalized_name=norm)
        if domain and not is_shared_sender_domain(domain):
            company.website_domain = domain.lower()
        db.add(company)
        db.flush()
    return company


@dataclass
class MatchCandidate:
    application: Application
    score: float
    reasons: list[str]
    disqualified: bool = False

    def as_dict(self) -> dict:
        a = self.application
        return {
            "application_id": a.id,
            "company": a.company.canonical_name if a.company else None,
            "role_title": a.role_title,
            "external_job_id": a.external_job_id,
            "stage": a.stage,
            "score": round(self.score, 3),
            "reasons": self.reasons,
            "disqualified": self.disqualified,
        }


@dataclass
class MatchResult:
    decision: str  # auto | review | none
    best: Application | None
    score: float
    reason: str
    candidates: list[MatchCandidate] = field(default_factory=list)
    has_role_evidence: bool = False

    def alternatives(self) -> list[dict]:
        return [c.as_dict() for c in self.candidates]


def _thread_application_ids(db: Session, thread_id: str | None, account_id: str | None) -> set[str]:
    if not thread_id:
        return set()
    stmt = (
        select(ApplicationEvent.application_id)
        .join(EmailMessage, EmailMessage.id == ApplicationEvent.email_message_id)
        .where(
            EmailMessage.provider_thread_id == thread_id,
            ApplicationEvent.application_id.is_not(None),
            ApplicationEvent.is_approved.is_(True),
        )
    )
    if account_id:
        stmt = stmt.where(EmailMessage.email_account_id == account_id)
    return {r for r in db.execute(stmt).scalars().all() if r}


def match_application(
    db: Session,
    *,
    company: Company | None,
    role_title: str | None,
    requisition_id: str | None,
    job_url: str | None = None,
    thread_id: str | None = None,
    account_id: str | None = None,
    event_at: datetime | None = None,
) -> MatchResult:
    req = normalize_req_id(requisition_id)
    url = canonical_url(job_url)
    has_title = bool(role_title) and not is_placeholder_title(role_title)
    has_role_evidence = bool(req or url or has_title)

    pool: list[Application] = []
    if company is not None:
        pool = list(
            db.execute(select(Application).where(Application.company_id == company.id)).scalars().all()
        )
    elif req:
        # Unknown company, but an exact requisition id is strong cross-company evidence only if unique.
        pool = list(
            db.execute(select(Application).where(Application.external_job_id == req)).scalars().all()
        )
    if not pool:
        return MatchResult("none", None, 0.0, "no existing application for this company", [], has_role_evidence)

    thread_apps = _thread_application_ids(db, thread_id, account_id)
    cands: list[MatchCandidate] = []
    for app in pool:
        score = 0.3 if company is not None else 0.0
        reasons = ["same company"] if company is not None else []
        dq = False
        app_req = normalize_req_id(app.external_job_id)
        if req and app_req:
            if req == app_req:
                score += 0.6
                reasons.append(f"requisition {req} matches")
            else:
                dq = True
                reasons.append(f"different requisition ({app_req} vs {req})")
        if url and app.canonical_posting_url:
            if url == app.canonical_posting_url:
                score += 0.5
                reasons.append("posting URL matches")
        if has_title:
            sim = title_similarity(app.role_title, role_title)
            if sim >= 0.92:
                score += 0.55
                reasons.append(f"role title matches ({sim:.2f})")
            elif sim >= 0.75:
                score += 0.3
                reasons.append(f"role title similar ({sim:.2f})")
            elif sim < 0.5 and not is_placeholder_title(app.role_title):
                score -= 0.3
                reasons.append(f"role title differs ({sim:.2f})")
        if app.id in thread_apps:
            score += 0.3
            reasons.append("same email thread as earlier evidence")
        if dq:
            score = 0.0
        cands.append(MatchCandidate(app, max(0.0, min(score, 1.0)), reasons, dq))

    cands.sort(key=lambda c: c.score, reverse=True)
    viable = [c for c in cands if not c.disqualified and c.score > 0.3 - 1e-9]
    if not viable:
        return MatchResult(
            "none", None, 0.0, "no application matches the role/requisition", cands, has_role_evidence
        )
    top = viable[0]
    second = viable[1].score if len(viable) > 1 else 0.0
    strong_evidence = any(
        r.startswith(("requisition", "posting URL", "role title matches", "same email thread"))
        for r in top.reasons
    )
    if not has_role_evidence and not any(r.startswith("same email thread") for r in top.reasons):
        return MatchResult(
            "review",
            top.application,
            top.score,
            "company-only evidence; the email does not identify the role",
            cands,
            has_role_evidence,
        )
    if top.score >= AUTO_MATCH_MIN_SCORE and (top.score - second) >= AUTO_MATCH_MIN_MARGIN and strong_evidence:
        return MatchResult("auto", top.application, top.score, "; ".join(top.reasons), cands, has_role_evidence)
    if top.score < 0.5 and has_role_evidence:
        return MatchResult(
            "none", None, top.score, "role evidence does not match any existing application", cands, has_role_evidence
        )
    return MatchResult(
        "review",
        top.application,
        top.score,
        "ambiguous: " + "; ".join(top.reasons) + (f" (runner-up {second:.2f})" if second else ""),
        cands,
        has_role_evidence,
    )
