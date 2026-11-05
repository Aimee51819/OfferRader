"""Reproducible demo on synthetic mail (no network, no Ollama, no real data).

Feeds the fixture corpus through the real pipeline in received order using the
replay backend, approving 'create application' proposals for acknowledgments
(as a user would) and leaving everything else in the review queue.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from ..db import Base, SessionLocal, engine, init_db
from ..models import EmailAccount, ReviewProposal
from . import settings_service
from .llm.offline import ReplayBackend
from .mail.fake import to_provider_message
from .notifications import evaluate
from .pipeline.ingest import ingest_message
from .pipeline.process import process_pending
from .pipeline.review import approve

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "emails"
_ACK = {"application_confirmation", "application_received"}


def load_demo(reset: bool = False) -> dict:
    init_db()
    if reset:
        with engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            for t in reversed(Base.metadata.sorted_tables):
                if t.name != "credentials":
                    conn.execute(t.delete())
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    items = sorted(
        (json.loads(f.read_text(encoding="utf-8")) for f in FIXTURES.glob("*.json")),
        key=lambda i: i["received_at"],
    )
    backend = ReplayBackend({i["subject"]: i["model_output"] for i in items})
    db = SessionLocal()
    try:
        settings_service.seed_defaults(db)
        settings_service.set_value(db, "auto_apply_policy", "auto_harmless")
        accounts = {}
        for name in ("primary", "secondary"):
            addr = f"demo.{name}@example.edu"
            acc = db.execute(select(EmailAccount).where(EmailAccount.address == addr)).scalar_one_or_none()
            if acc is None:
                acc = EmailAccount(provider="fake", address=addr, display_name=f"Demo {name} inbox",
                                   auth_method="none", enabled=False,
                                   sync_scope={"fixture_dir": str(FIXTURES), "fixture_account": name})
                db.add(acc)
                db.flush()
            accounts[name] = acc
        db.commit()
        approved = 0
        for item in items:
            ingest_message(db, accounts[item.get("account", "primary")], to_provider_message(item))
            db.commit()
            process_pending(db, backend)
            for p in db.execute(select(ReviewProposal).where(ReviewProposal.state == "pending")).scalars().all():
                types = {e["type"] for e in p.proposed_changes.get("events", [])}
                if p.proposal_type == "create_application" and types & _ACK:
                    approve(db, p.id, actor="demo")
                    approved += 1
            db.commit()
        evaluate(db)
        pending = db.execute(select(ReviewProposal).where(ReviewProposal.state == "pending")).scalars().all()
        return {
            "messages": len(items),
            "auto_approved_creations": approved,
            "pending_reviews": [{"type": p.proposal_type, "reason": p.reason[:120]} for p in pending],
            "note": "Synthetic data only. Model outputs are hand-written replays, not Qwen results.",
        }
    finally:
        db.close()
