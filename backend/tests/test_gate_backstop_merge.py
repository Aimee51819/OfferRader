"""Default-deny job gate, rule backstop for missed lifecycle mail, and proposal merging."""
from __future__ import annotations

from sqlalchemy import select

from app.models import EmailMessage
from app.services.llm.offline import ReplayBackend
from app.services.mail.prefilter import has_job_signal
from app.services.pipeline.review import approve

from .helpers import approve_all, apps, deliver, pending


def _fx(fixtures, name):
    return fixtures[name]


# Gate ------------------------------------------------------------------------------------------
def test_gate_passes_every_fixture_with_an_application_event(fixtures):
    for f in fixtures.values():
        actionable = [e for e in f["expected"]["events"] if e not in ("job_alert", "other")]
        if actionable:
            sig = has_job_signal(f"{f['from_name']} <{f['from_address']}>", f["subject"], f["body"])
            assert sig is not None, f["name"]


def test_gate_keeps_newsletters_and_events_away_from_the_model(db, fixtures):
    backend = ReplayBackend({})
    for name in ("06_job_alert", "25_hackathon_registration", "26_portfolio_review_event",
                 "27_jobboard_hired_near_you"):
        deliver(db, backend, fixtures, name)
    assert backend.calls == 0
    states = db.execute(select(EmailMessage.processing_state)).scalars().all()
    assert states and set(states) == {"ignored"}
    assert pending(db) == []


def test_gate_recognises_ats_senders_and_chinese_wording():
    assert has_job_signal("Acme <no-reply@us.greenhouse-mail.io>", "Update", "") == "ats_sender"
    assert has_job_signal("Bank <noreply@x.tal.net>", "Hello", "") == "ats_sender"
    assert has_job_signal("HR <hr@corp.example>", "感谢应聘某公司", "") == "lifecycle_phrase"
    assert has_job_signal("Shop <deals@shop.example>", "Special offer: 10% off", "") is None


# Backstop -------------------------------------------------------------------------------------
def test_backstop_turns_missed_ack_and_rejection_into_review_items(db, fixtures):
    # Simulate a small model that wrongly says "not job related" with no events.
    empty = {"schema_version": "1.0", "is_job_related": False, "events": []}
    subjects = [_fx(fixtures, n)["subject"] for n in ("28_short_ack_no_role", "29_zh_rejection")]
    backend = ReplayBackend({s: empty for s in subjects})
    deliver(db, backend, fixtures, "28_short_ack_no_role", "29_zh_rejection")
    props = pending(db)
    assert len(props) == 2
    types = sorted(e["type"] for p in props for e in p.proposed_changes["events"])
    assert types == ["application_confirmation", "rejection"]
    assert all(p.proposed_changes["model_needs_review"] for p in props)
    assert apps(db) == []  # nothing is applied without approval


def test_backstop_does_not_fire_for_event_registration(db, fixtures):
    from app.config import settings

    settings.job_signal_gate = False  # let it reach the "model" to exercise the backstop only
    try:
        empty = {"schema_version": "1.0", "is_job_related": False, "events": []}
        f = _fx(fixtures, "26_portfolio_review_event")
        deliver(db, ReplayBackend({f["subject"]: empty}), fixtures, f["name"])
    finally:
        settings.job_signal_gate = True
    assert pending(db) == []


# Merging --------------------------------------------------------------------------------------
def test_pending_create_proposals_with_same_requisition_merge_on_approval(db, replay, fixtures):
    deliver(db, replay, fixtures, "01_ibm_pm_ack", "03_ibm_pm_oa")  # both req 84211, nothing approved yet
    props = pending(db)
    assert [p.proposal_type for p in props] == ["create_application", "create_application"]
    first = next(p for p in props if "84211" in (p.proposed_changes["role"]["requisition_id"] or ""))
    approve(db, first.id)
    db.commit()
    (other,) = pending(db)
    assert other.proposal_type == "match"
    assert other.target_application_id == first.target_application_id
    approve_all(db)
    assert len(apps(db, "IBM")) == 1
