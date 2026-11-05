from __future__ import annotations

from sqlalchemy import select

from app.models import (
    ActionItem,
    Application,
    ApplicationEvent,
    Company,
    EmailAccount,
    ReviewProposal,
)
from app.services import settings_service
from app.services.mail.fake import to_provider_message
from app.services.pipeline.ingest import ingest_message
from app.services.pipeline.process import process_pending
from app.services.pipeline.review import approve


def account(db, address="aimee.primary@example.edu", provider="fake"):
    acc = db.execute(select(EmailAccount).where(EmailAccount.address == address)).scalar_one_or_none()
    if acc is None:
        acc = EmailAccount(provider=provider, address=address, auth_method="none", sync_scope={})
        db.add(acc)
        db.commit()
    return acc


def deliver(db, backend, fixtures, *names, acc=None, policy="auto_harmless"):
    settings_service.set_value(db, "auto_apply_policy", policy)
    db.commit()
    acc = acc or account(db)
    for n in names:
        ingest_message(db, acc, to_provider_message(fixtures[n]))
    db.commit()
    return process_pending(db, backend)


def pending(db):
    return db.execute(select(ReviewProposal).where(ReviewProposal.state == "pending")).scalars().all()


def approve_all(db, **kw):
    out = []
    for p in pending(db):
        out.append(approve(db, p.id, **kw))
    db.commit()
    return out


def apps(db, company=None):
    stmt = select(Application)
    if company:
        stmt = stmt.join(Company).where(Company.canonical_name == company)
    return db.execute(stmt).scalars().all()


def app_by_title(db, company, title_fragment):
    found = [a for a in apps(db, company) if title_fragment.lower() in a.role_title.lower()]
    assert len(found) == 1, [a.role_title for a in apps(db, company)]
    return found[0]


def items(db, app):
    return db.execute(select(ActionItem).where(ActionItem.application_id == app.id)).scalars().all()


def events(db, app, etype=None):
    stmt = select(ApplicationEvent).where(ApplicationEvent.application_id == app.id)
    if etype:
        stmt = stmt.where(ApplicationEvent.event_type == etype)
    return db.execute(stmt).scalars().all()
