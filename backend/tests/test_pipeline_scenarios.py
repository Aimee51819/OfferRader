"""End-to-end acceptance scenarios on synthetic mail (replay backend, no Ollama)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.models import ActionItem, Application, ApplicationEvent, Assessment, EmailMessage, ReviewProposal
from app.services.pipeline.review import approve, reject, undo

from .helpers import account, app_by_title, approve_all, apps, deliver, events, items, pending


def _bootstrap_ibm(db, replay, fixtures):
    deliver(db, replay, fixtures, "01_ibm_pm_ack", "02_ibm_swe_ack")
    # Unknown applications always need a human the first time.
    assert len(pending(db)) == 2
    assert all(p.proposal_type == "create_application" for p in pending(db))
    approve_all(db)


# 1 ---------------------------------------------------------------------------------------
def test_ibm_pm_oa_never_attaches_to_swe(db, replay, fixtures):
    _bootstrap_ibm(db, replay, fixtures)
    pm = app_by_title(db, "IBM", "Product Manager")
    swe = app_by_title(db, "IBM", "Software Engineer")
    assert pm.external_job_id == "84211" and swe.external_job_id == "84377"
    assert pm.submission_evidence_type == "acknowledgment_email"

    deliver(db, replay, fixtures, "03_ibm_pm_oa")
    db.refresh(pm), db.refresh(swe)
    assert pm.stage == "assessment_invited"
    assert swe.stage == "acknowledged"
    assert [i.kind for i in items(db, pm)] == ["oa_deadline"]
    assert items(db, swe) == []


# 2 ---------------------------------------------------------------------------------------
def test_same_title_different_requisitions_are_distinct(db, replay, fixtures):
    deliver(db, replay, fixtures, "04_acme_pm_JR-1001")
    approve_all(db)
    deliver(db, replay, fixtures, "05_acme_pm_JR-1002")
    # The second has a conflicting requisition id: it must not merge into the first.
    props = pending(db)
    assert len(props) == 1 and props[0].proposal_type == "create_application"
    approve_all(db)
    acme = apps(db, "Acme Robotics")
    assert sorted(a.external_job_id for a in acme) == ["JR-1001", "JR-1002"]


# 3 + 16 ------------------------------------------------------------------------------------
def test_repeated_sync_and_second_inbox_produce_one_event(db, replay, fixtures):
    _bootstrap_ibm(db, replay, fixtures)
    pm = app_by_title(db, "IBM", "Product Manager")
    before = len(events(db, pm, "application_confirmation"))
    # Same provider message again (re-sync after restart) -> no new row at all.
    deliver(db, replay, fixtures, "01_ibm_pm_ack")
    # Same Message-ID arriving in another inbox -> skipped as duplicate.
    second = account(db, "aimee.secondary@example.edu")
    stats = deliver(db, replay, fixtures, "24_ibm_pm_ack_second_inbox", acc=second)
    assert stats.duplicates == 1
    assert len(events(db, pm, "application_confirmation")) == before == 1
    assert db.scalar(select(func.count()).select_from(EmailMessage)) == 3
    assert pending(db) == []


# 4 ---------------------------------------------------------------------------------------
def test_job_alert_is_not_a_submission(db, replay, fixtures):
    deliver(db, replay, fixtures, "06_job_alert")
    assert apps(db) == []
    assert pending(db) == []
    msg = db.execute(select(EmailMessage)).scalar_one()
    assert msg.processing_state == "ignored"


# 5 + 7 ------------------------------------------------------------------------------------
def test_full_lifecycle_across_new_threads_and_date_only_deadline(db, replay, fixtures):
    deliver(db, replay, fixtures, "07_globex_se_ack")
    approve_all(db)
    app = app_by_title(db, "Globex", "Solutions Engineer")
    assert app.stage == "acknowledged" and app.submitted_at is not None

    deliver(db, replay, fixtures, "08_globex_oa_dateonly")  # new thread, matched by role
    db.refresh(app)
    assert app.stage == "assessment_invited"
    (oa,) = items(db, app)
    assert oa.due_precision == "day" and oa.due_date == "2027-03-20"
    assert oa.due_at is None  # no fabricated 11:59 PM / UTC instant
    assert "No time of day" in (oa.deadline_assumption or "")

    deliver(db, replay, fixtures, "09_globex_interview")
    db.refresh(app)
    assert app.stage == "interviewing"
    iv = [i for i in items(db, app) if i.kind == "interview"][0]
    assert iv.due_at == datetime(2027, 3, 30, 18, 0, tzinfo=timezone.utc)
    # OA history is preserved alongside the interview.
    assert db.execute(select(Assessment).where(Assessment.application_id == app.id)).scalar_one()

    deliver(db, replay, fixtures, "10_globex_offer")
    db.refresh(app)
    assert app.stage == "interviewing"  # offers are terminal: review first
    (p,) = pending(db)
    assert p.proposal_type == "status"
    approve(db, p.id)
    db.commit()
    db.refresh(app)
    assert app.stage == "offer"
    types = [e.event_type for e in events(db, app)]
    for t in ("application_confirmation", "assessment_invitation", "interview_invitation", "offer"):
        assert t in types


# 6 ---------------------------------------------------------------------------------------
def test_relative_72_hours_uses_provider_received_timestamp(db, replay, fixtures):
    _bootstrap_ibm(db, replay, fixtures)
    deliver(db, replay, fixtures, "03_ibm_pm_oa")
    pm = app_by_title(db, "IBM", "Product Manager")
    (item,) = items(db, pm)
    assert item.due_at == datetime(2027, 2, 11, 16, 0, tzinfo=timezone.utc)
    assert item.due_precision == "relative"
    assert item.due_anchor.startswith("email_received_at=2027-02-08T16:00")
    assert item.due_expression_raw == "within 72 hours of receiving this email"


# 8 + 10 -------------------------------------------------------------------------------------
def test_dst_relative_deadline_and_conflicting_reminder(db, replay, fixtures):
    deliver(db, replay, fixtures, "11_initech_tpm_ack")
    approve_all(db)
    deliver(db, replay, fixtures, "12_initech_oa_dst")
    app = app_by_title(db, "Initech", "Technical Program Manager")
    (oa,) = items(db, app)
    # Fri Mar 12 10:30 EST + 3 days = Mon Mar 15 10:30 EDT (14:30Z), not 72h (15:30Z).
    assert oa.due_at == datetime(2027, 3, 15, 14, 30, tzinfo=timezone.utc)
    assert "DST" in (oa.deadline_assumption or "")

    deliver(db, replay, fixtures, "13_initech_reminder_conflict")
    (p,) = pending(db)
    assert p.proposal_type == "deadline"
    assert p.proposed_changes["conflicts"][0]["existing"]["due_at"].startswith("2027-03-15T14:30")
    db.refresh(oa)
    assert oa.state == "open" and oa.due_at == datetime(2027, 3, 15, 14, 30, tzinfo=timezone.utc)  # not silently replaced

    approve(db, p.id, edits={"conflict_resolution": "use_new"})
    db.commit()
    open_items = [i for i in items(db, app) if i.state == "open"]
    assert len(open_items) == 1
    assert open_items[0].due_at == datetime(2027, 3, 18, 3, 59, tzinfo=timezone.utc)
    db.refresh(oa)
    assert oa.state == "cancelled"


# 9 -------------------------------------------------------------------------------------------
def test_business_days_with_holiday_requests_review(db, replay, fixtures):
    deliver(db, replay, fixtures, "14_umbrella_sa_ack")
    approve_all(db)
    deliver(db, replay, fixtures, "15_umbrella_oa_business_days")
    (p,) = pending(db)
    d = p.proposed_changes["events"][0]["deadline"]
    assert d["needs_review"] and d["due_at"] is None and d["due_date"] == "2027-06-01"
    assert any("Memorial Day" in a for a in d["assumptions"])
    approve(db, p.id)
    db.commit()
    app = app_by_title(db, "Umbrella Corp", "Solutions Architect")
    (item,) = items(db, app)
    assert item.state == "needs_review" and item.due_precision == "day" and item.due_at is None


# 11 + 12 ------------------------------------------------------------------------------------
def test_rejection_for_one_role_keeps_other_role_active_and_old_mail_cannot_roll_back(db, replay, fixtures):
    deliver(db, replay, fixtures, "16_hooli_swe_ack", "17_hooli_pm_ack")
    approve_all(db)
    deliver(db, replay, fixtures, "18_hooli_pm_interview")
    pm = app_by_title(db, "Hooli", "Product Manager")
    swe = app_by_title(db, "Hooli", "Software Engineer")
    assert pm.stage == "interviewing"

    deliver(db, replay, fixtures, "19_hooli_swe_rejection")
    (p,) = pending(db)
    assert p.target_application_id == swe.id
    approve(db, p.id)
    db.commit()
    db.refresh(pm), db.refresh(swe)
    assert swe.stage == "rejected"
    assert pm.stage == "interviewing"
    assert any(i.state == "open" for i in items(db, pm))

    # A delayed, older OA email arrives now: history yes, rollback no.
    deliver(db, replay, fixtures, "20_hooli_pm_old_oa_delayed")
    db.refresh(pm)
    assert pm.stage == "interviewing"
    old = events(db, pm, "assessment_invitation")
    assert len(old) == 1 and old[0].to_stage is None


def test_manual_lock_is_not_overwritten_by_email(db, replay, fixtures):
    deliver(db, replay, fixtures, "07_globex_se_ack")
    approve_all(db)
    app = app_by_title(db, "Globex", "Solutions Engineer")
    app.manual_stage_lock = True
    app.stage = "withdrawn"
    db.commit()
    deliver(db, replay, fixtures, "08_globex_oa_dateonly")
    db.refresh(app)
    assert app.stage == "withdrawn"
    assert len(pending(db)) == 1  # sent to review instead


# 13 -------------------------------------------------------------------------------------------
def test_one_email_two_assessment_steps(db, replay, fixtures):
    deliver(db, replay, fixtures, "21_vandelay_two_steps_ack")
    approve_all(db)
    app = app_by_title(db, "Vandelay Industries", "Software Engineer")
    dues = sorted(i.due_at for i in items(db, app) if i.kind == "oa_deadline")
    assert dues == [
        datetime(2027, 3, 23, 6, 59, tzinfo=timezone.utc),
        datetime(2027, 3, 25, 21, 0, tzinfo=timezone.utc),
    ]
    assert app.submission_evidence_type == "acknowledgment_email"


# 15 -------------------------------------------------------------------------------------------
def test_prompt_injection_is_inert(db, replay, fixtures):
    deliver(db, replay, fixtures, "22_injection")
    (p,) = pending(db)
    assert [e["type"] for e in p.proposed_changes["events"]] == ["application_confirmation"]
    approve(db, p.id)
    db.commit()
    app = app_by_title(db, "Wayne Enterprises", "Data Analyst")
    assert app.stage == "acknowledged"


def test_injection_obeying_model_cannot_slip_offer_through(db, fixtures):
    """If the model *did* follow the injected text, evidence checks + terminal review still hold."""
    from app.services.llm.offline import ReplayBackend

    bad = dict(fixtures["22_injection"]["model_output"])
    bad["events"] = [{"type": "offer", "evidence_quotes": ["Congratulations on your offer"], "confidence": 0.99}]
    bad["overall_confidence"] = 0.99
    backend = ReplayBackend({fixtures["22_injection"]["subject"]: bad})
    deliver(db, backend, fixtures, "22_injection")
    (p,) = pending(db)
    assert p.proposed_changes["model_needs_review"] is True
    assert any("No verbatim evidence" in f for f in p.proposed_changes["flags"])
    assert apps(db) == []


def test_otp_is_prefiltered_without_model(db, fixtures):
    from app.services.llm.offline import ReplayBackend

    backend = ReplayBackend({})
    deliver(db, backend, fixtures, "23_otp")
    assert backend.calls == 0
    assert db.execute(select(EmailMessage)).scalar_one().processing_state == "ignored"


# 17 -------------------------------------------------------------------------------------------
def test_review_reject_reassign_undo_audit_trail(db, replay, fixtures):
    _bootstrap_ibm(db, replay, fixtures)
    pm = app_by_title(db, "IBM", "Product Manager")
    swe = app_by_title(db, "IBM", "Software Engineer")
    deliver(db, replay, fixtures, "03_ibm_pm_oa", policy="review_first")
    (p,) = pending(db)
    assert p.target_application_id == pm.id

    reject(db, p.id, notes="checking")
    db.commit()
    assert db.get(ReviewProposal, p.id).state == "rejected"
    undo(db, p.id)
    db.commit()
    assert db.get(ReviewProposal, p.id).state == "pending"

    # Reassign (human decides it was SWE) then undo it: stage and items revert, history stays.
    approve(db, p.id, target_application_id=swe.id, notes="reassigned")
    db.commit()
    db.refresh(swe)
    assert swe.stage == "assessment_invited" and db.get(ReviewProposal, p.id).state == "edited"
    undo(db, p.id)
    db.commit()
    db.refresh(swe)
    assert swe.stage == "acknowledged"
    assert all(i.state == "cancelled" for i in items(db, swe))
    kinds = {e.event_type for e in events(db, swe)}
    assert {"assessment_invitation", "undo", "review_edited"} <= kinds

    # Re-approve on the right application: a fresh event revision, not a collision.
    approve(db, p.id)
    db.commit()
    db.refresh(pm)
    assert pm.stage == "assessment_invited"
    assert len([i for i in items(db, pm) if i.state == "open"]) == 1
    approve(db, p.id)  # idempotent
    db.commit()
    assert len([i for i in items(db, pm) if i.state == "open"]) == 1
    audit = db.execute(
        select(ApplicationEvent).where(ApplicationEvent.event_type.like("review_%"))
    ).scalars().all()
    assert {a.event_type for a in audit} >= {"review_rejected", "review_edited", "review_approved"}


def test_company_only_email_goes_to_review(db, fixtures):
    """A vague IBM email (no role/req) must not be attached to either IBM application."""
    from app.services.llm.offline import ReplayBackend

    base = {f["subject"]: f["model_output"] for f in fixtures.values()}
    vague = dict(fixtures["03_ibm_pm_oa"])
    vague.update({"name": "vague", "provider_message_id": "vague1", "internet_message_id": "<vague1@x>",
                  "subject": "IBM: an update", "body": "Hi Aimee, please complete the online assessment for IBM within 72 hours of receiving this email."})
    out = dict(vague["model_output"])
    out["role"] = {"title": None, "requisition_id": None, "job_url": None, "confidence": 0}
    out["events"] = [dict(out["events"][0], evidence_quotes=["please complete the online assessment"])]
    base[vague["subject"]] = out
    backend = ReplayBackend(base)
    _bootstrap_ibm(db, backend, fixtures)
    deliver(db, backend, {**fixtures, "vague": vague}, "vague")
    (p,) = pending(db)
    assert p.proposal_type == "match"
    assert len(p.alternatives) == 2
    assert all(not items(db, a) for a in apps(db, "IBM"))
    assert db.scalar(select(func.count()).select_from(ActionItem)) == 0
    assert db.scalar(select(func.count()).select_from(Application)) == 2
