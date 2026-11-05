"""REST API: preflight, CRUD, review flow, CSV/JSON/ICS, integration endpoint, local-only guard."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import ActionItem, ApplicationEvent, NotificationLog
from app.services.notifications import evaluate

from .helpers import deliver

TOKEN = {"Authorization": "Bearer test-integration-token"}


def _create(client, **kw):
    body = {"company": "IBM", "role_title": "Product Manager Intern", "external_job_id": "84211",
            "confirmed_submitted": True, "submitted_at": "2026-10-01T14:00:00Z", **kw}
    return client.post("/api/applications", json=body)


def test_preflight_block_warn_allow_and_override(client):
    assert _create(client).status_code == 201
    check = lambda **b: client.post("/api/duplicates/check", json=b).json()  # noqa: E731
    assert check(company="IBM", role_title="Product Manager Intern", external_job_id="84211")["decision"] == "BLOCK"
    warn = check(company="IBM", role_title="Software Engineer Intern", external_job_id="84377")
    assert warn["decision"] == "WARN" and warn["matches"][0]["match_kind"] == "other_role_same_company"
    assert check(company="Initech", role_title="TPM Intern")["decision"] == "ALLOW"

    r = _create(client)
    assert r.status_code == 409 and r.json()["detail"]["duplicate_check"]["decision"] == "BLOCK"
    r = _create(client, override_duplicate=True, override_reason="re-applied after recruiter asked")
    assert r.status_code == 201
    evs = client.get(f"/api/applications/{r.json()['id']}/events").json()
    assert any(e["event_type"] == "duplicate_override" and "recruiter asked" in e["description"] for e in evs)


def test_verified_vs_unverified_and_stage_guard(client):
    tracked = client.post("/api/applications", json={"company": "Globex", "role_title": "SE Intern"}).json()
    assert tracked["verified"] is False and tracked["stage"] == "tracked"
    assert client.post("/api/applications", json={"company": "X", "role_title": "Y", "stage": "interviewing"}).status_code == 422
    verified = _create(client).json()
    assert verified["verified"] is True and verified["submission_evidence_type"] == "manual_confirmed"
    only_unverified = client.get("/api/applications", params={"verified": "false"}).json()
    assert [a["id"] for a in only_unverified] == [tracked["id"]]

    r = client.post(f"/api/applications/{verified['id']}/stage", json={"stage": "withdrawn", "lock": True})
    assert r.json()["stage"] == "withdrawn" and r.json()["manual_stage_lock"] is True
    detail = client.get(f"/api/applications/{verified['id']}").json()
    assert detail["events"][0]["event_type"] == "manual_stage_change"
    future = _create(client, external_job_id="999", submitted_at="2031-01-01T00:00:00Z")
    assert future.status_code == 422


def test_csv_import_dry_run_duplicates_and_export_roundtrip(client):
    _create(client)
    rows = [
        {"company": "IBM", "role_title": "Product Manager Intern", "external_job_id": "84211", "stage": "submitted",
         "submitted_at": "2026-10-01"},
        {"company": "Hooli", "role_title": "SWE Intern", "stage": "submitted", "submitted_at": "2026-10-03T10:00:00-04:00"},
        {"company": "Bad", "role_title": "Row", "stage": "interviewing"},
    ]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["company", "role_title", "external_job_id", "stage", "submitted_at"])
    w.writeheader()
    w.writerows(rows)
    data = buf.getvalue().encode()

    dry = client.post("/api/import/csv", files={"file": ("a.csv", data, "text/csv")}, data={"dry_run": "true"}).json()
    assert [r["action"] for r in dry["rows"]] == ["would_skip", "would_import", "error"]
    assert len(client.get("/api/applications").json()) == 1

    real = client.post("/api/import/csv", files={"file": ("a.csv", data, "text/csv")}, data={"dry_run": "false"}).json()
    assert [r["action"] for r in real["rows"]] == ["skipped_duplicate", "imported", "error"]
    # Importing the same file again: everything is now a duplicate or an error.
    again = client.post("/api/import/csv", files={"file": ("a.csv", data, "text/csv")}, data={"dry_run": "false"}).json()
    assert again["created"] == 0

    exported = client.get("/api/export/applications.csv").text
    parsed = list(csv.DictReader(io.StringIO(exported)))
    assert {r["company"] for r in parsed} == {"IBM", "Hooli"}
    hooli = [r for r in parsed if r["company"] == "Hooli"][0]
    assert hooli["verified"] == "True" and hooli["submission_evidence_type"] == "manual_confirmed"
    detail = client.get(f"/api/applications/{hooli['id']}").json()
    assert detail["events"][-1]["source"] == "import" and detail["events"][-1]["actor"] == "user:csv_import"
    assert client.get("/api/export/json").json()["applications"]


def test_integration_submission_token_idempotency_and_block(client):
    body = {"source": "career-ops", "idempotency_key": "sub-0001-abcdef", "confirmed": True, "receipt_id": "CONF-77",
            "company": "Vandelay Industries", "role_title": "Software Engineer Intern", "external_job_id": "VAN-2207",
            "submitted_at": "2026-10-20T15:00:00Z"}
    assert client.post("/api/integrations/submissions", json=body).status_code == 401
    assert client.post("/api/integrations/submissions", json=body, headers={"Authorization": "Bearer nope"}).status_code == 401
    r1 = client.post("/api/integrations/submissions", json=body, headers=TOKEN)
    r2 = client.post("/api/integrations/submissions", json=body, headers=TOKEN)
    assert r1.status_code == 200 and r1.json()["created"] is True
    assert r2.json() == {**r2.json(), "created": False, "application_id": r1.json()["application_id"]}
    app = client.get(f"/api/applications/{r1.json()['application_id']}").json()
    assert app["submission_evidence_type"] == "agent_receipt" and app["verified"]

    other_key = {**body, "idempotency_key": "sub-0002-abcdef"}
    blocked = client.post("/api/integrations/submissions", json=other_key, headers=TOKEN)
    assert blocked.status_code == 409
    not_confirmed = {**body, "idempotency_key": "sub-0003-abcdef", "confirmed": False}
    assert client.post("/api/integrations/submissions", json=not_confirmed, headers=TOKEN).status_code == 422
    pre = client.post("/api/integrations/preflight", json={"company": "Vandelay Industries", "role_title": "x",
                                                           "external_job_id": "VAN-2207"}, headers=TOKEN)
    assert pre.json()["decision"] == "BLOCK"


def test_integration_upgrades_tracked_record_instead_of_duplicating(client):
    t = client.post("/api/applications", json={"company": "Globex", "role_title": "SE Intern", "external_job_id": "G-1"}).json()
    body = {"source": "career-ops", "idempotency_key": "sub-glob-0001", "confirmed": True, "receipt_id": "R1",
            "company": "Globex", "role_title": "SE Intern", "external_job_id": "G-1", "submitted_at": "2026-10-20T15:00:00Z"}
    r = client.post("/api/integrations/submissions", json=body, headers=TOKEN).json()
    assert r["application_id"] == t["id"] and r["created"] is False
    assert client.get(f"/api/applications/{t['id']}").json()["stage"] == "submitted"


def test_local_only_guard(client):
    assert client.get("/api/identity", headers={"Host": "evil.example"}).status_code == 403
    r = client.post("/api/applications", json={"company": "A", "role_title": "B"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    ok = client.get("/api/identity")
    assert ok.status_code == 200 and ok.headers["X-Frame-Options"] == "DENY"


def test_review_api_dashboard_deadlines_and_ics(client, db, replay, fixtures):
    deliver(db, replay, fixtures, "21_vandelay_two_steps_ack", policy="review_first")
    reviews = client.get("/api/reviews").json()
    assert len(reviews) == 1 and reviews[0]["email"]["excerpt"]
    pend = client.get("/api/dashboard/deadlines").json()["pending_review"]
    assert len(pend) == 2  # deadlines visible even before approval
    r = client.post(f"/api/reviews/{reviews[0]['id']}/approve", json={})
    assert r.status_code == 200 and r.json()["state"] == "approved"
    summary = client.get("/api/dashboard/summary").json()
    assert summary["pending_oa"] == 2 and summary["review_needed"] == 0
    ics = client.get("/api/export/deadlines.ics").text
    assert ics.count("BEGIN:VEVENT") == 2 and "DTSTART:20270323T065900Z" in ics
    undo = client.post(f"/api/reviews/{reviews[0]['id']}/undo").json()
    assert undo["state"] == "pending"
    changes = client.get("/api/dashboard/changes", params={"since": "2020-01-01T00:00:00Z"}).json()
    assert any(e["event_type"] == "undo" for e in changes["events"])


def test_reminders_fire_once_per_threshold_and_skip_after_sleep(client, db):
    app_id = client.post("/api/applications", json={"company": "Hooli", "role_title": "APM Intern"}).json()["id"]
    due = datetime.now(timezone.utc) + timedelta(hours=2)
    db.add(ActionItem(application_id=app_id, kind="oa_deadline", title="Complete OA", due_at=due,
                      due_precision="instant", state="open"))
    db.commit()
    fired = evaluate(db)
    assert len(fired) == 1  # 3h rule only; 72h/24h were missed while "asleep" and are skipped
    assert evaluate(db) == [] and evaluate(db) == []
    logs = db.execute(select(NotificationLog)).scalars().all()
    assert sorted(l.delivery_state for l in logs) == ["delivered", "skipped", "skipped"]
    assert len(client.get("/api/dashboard/notifications").json()) == 1


def test_settings_validation_and_llm_health_offline(client):
    assert client.put("/api/settings", json={"ollama_base_url": "http://203.0.113.5:11434"}).status_code == 422
    assert client.put("/api/settings", json={"display_timezone": "Mars/Base"}).status_code == 422
    s = client.put("/api/settings", json={"auto_apply_policy": "auto_harmless"}).json()
    assert s["auto_apply_policy"] == "auto_harmless" and "gmail_redirect_uri" in s["oauth"]
    h = client.get("/api/llm/health").json()
    assert h["ok"] is False and "ollama" in (h.get("hint") or "").lower()
    assert client.get("/api/oauth/gmail/start").status_code == 412  # not configured in tests


def test_manual_oa_completion_and_audit(client, db, replay, fixtures):
    deliver(db, replay, fixtures, "07_globex_se_ack")
    from .helpers import approve_all

    approve_all(db)
    deliver(db, replay, fixtures, "08_globex_oa_dateonly")
    app = client.get("/api/applications", params={"company": "Globex"}).json()[0]
    assert app["next_action"]["urgency"] in {"later", "overdue", "within_72h", "within_24h"}
    detail = client.get(f"/api/applications/{app['id']}").json()
    aid = detail["assessments"][0]["id"]
    out = client.post(f"/api/assessments/{aid}/complete").json()
    assert out["stage"] == "assessment_completed" and out["next_action"] is None
    assert db.execute(select(ApplicationEvent).where(ApplicationEvent.description.like("%manually%"))).first()
