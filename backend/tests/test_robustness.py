"""Scenario 14 + 16: malformed output, model down, auth expiry, rate limits, interruptions, restarts."""
from __future__ import annotations

import json

from sqlalchemy import func, select

from app.models import Application, ApplicationEvent, EmailAccount, EmailMessage, ParsedMessage, SyncRun
from app.services import sync as sync_svc
from app.services.llm.offline import ReplayBackend
from app.services.mail.base import AuthExpired, RateLimited
from app.services.mail.fake import FakeProvider

from .helpers import account, approve_all, deliver, pending


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_invalid_json_is_recorded_and_changes_nothing(db, fixtures):
    backend = ReplayBackend({fixtures["01_ibm_pm_ack"]["subject"]: "not json {{"})
    stats = deliver(db, backend, fixtures, "01_ibm_pm_ack")
    msg = db.execute(select(EmailMessage)).scalar_one()
    assert stats.failed == 1 and msg.processing_state == "failed"
    assert "invalid_json" in msg.processing_error
    assert _count(db, Application) == 0 and pending(db) == []
    assert backend.calls == 2  # one bounded retry with the validation error


def test_schema_violation_is_rejected(db, fixtures):
    bad = dict(fixtures["01_ibm_pm_ack"]["model_output"], events=[{"type": "hired!!", "confidence": 3}])
    backend = ReplayBackend({fixtures["01_ibm_pm_ack"]["subject"]: bad})
    deliver(db, backend, fixtures, "01_ibm_pm_ack")
    pm = db.execute(select(ParsedMessage)).scalar_one()
    assert pm.extracted_payload is None and any("schema" in e for e in pm.validation_errors)


def test_ollama_down_keeps_messages_pending_and_retries_later(db, fixtures, replay):
    down = ReplayBackend({}, unavailable=True)
    stats = deliver(db, down, fixtures, "01_ibm_pm_ack", "02_ibm_swe_ack")
    assert stats.aborted_reason == "llm_unavailable"
    states = db.execute(select(EmailMessage.processing_state, EmailMessage.processing_attempts)).all()
    assert all(s == ("pending", 0) for s in states)
    assert _count(db, ParsedMessage) == 0
    # Model comes back: same messages are processed once.
    from app.services.pipeline.process import process_pending

    process_pending(db, replay)
    assert len(pending(db)) == 2


def test_auth_expired_and_rate_limit_mark_run_without_losing_cursor(db, replay):
    acc = account(db)
    acc.sync_cursor = json.dumps({"offset": 3})
    db.commit()

    class Expired:
        def iter_pages(self, cursor, **kw):
            raise AuthExpired("internal-detail-xyz")
            yield  # pragma: no cover

    class Limited:
        def iter_pages(self, cursor, **kw):
            raise RateLimited("slow down", 30)
            yield  # pragma: no cover

    sync_svc.PROVIDER_OVERRIDES["fake"] = lambda db, a: Expired()
    try:
        run = sync_svc.sync_account(db, acc, backend=replay)
        db.refresh(acc)
        assert run.status == "failed" and acc.last_error_code == "auth_expired"
        assert "Reconnect" in run.safe_error_summary and "internal-detail-xyz" not in run.safe_error_summary
        sync_svc.PROVIDER_OVERRIDES["fake"] = lambda db, a: Limited()
        run = sync_svc.sync_account(db, acc, backend=replay)
        db.refresh(acc)
        assert run.status == "partial" and acc.last_error_code == "rate_limited"
        assert json.loads(acc.sync_cursor) == {"offset": 3}
    finally:
        sync_svc.PROVIDER_OVERRIDES.clear()


def test_network_interruption_resumes_from_last_committed_page_multi_account(db, fixtures, replay):
    primary = [f for f in fixtures.values() if f.get("account") == "primary"]
    a1 = account(db, "aimee.primary@example.edu")
    a2 = account(db, "aimee.secondary@example.edu")
    secondary = [f for f in fixtures.values() if f.get("account") == "secondary"]
    providers = {a1.id: FakeProvider(primary, page_size=5, fail_after_pages=2), a2.id: FakeProvider(secondary)}
    sync_svc.PROVIDER_OVERRIDES["fake"] = lambda db, a: providers[a.id]
    try:
        run = sync_svc.sync_account(db, a1, backend=replay)
        assert run.status == "partial" and run.messages_new == 10
        db.refresh(a1)
        assert json.loads(a1.sync_cursor) == {"offset": 10}
        # "Restart after sleep": a fresh provider without the fault continues at offset 10.
        providers[a1.id] = FakeProvider(primary, page_size=5)
        run = sync_svc.sync_account(db, a1, backend=replay)
        assert run.status == "success" and run.messages_new == len(primary) - 10
        run = sync_svc.sync_account(db, a2, backend=replay)
        # Repeat everything: nothing new anywhere.
        for a in (a1, a2):
            a.sync_cursor = None
        db.commit()
        for a in (a1, a2):
            assert sync_svc.sync_account(db, a, backend=replay).messages_new == 0
    finally:
        sync_svc.PROVIDER_OVERRIDES.clear()
    assert _count(db, EmailMessage) == len(fixtures)
    dup = db.execute(select(EmailMessage).where(EmailMessage.email_account_id == a2.id)).scalar_one()
    assert dup.processing_state == "skipped"


def test_interrupted_run_is_recovered_on_startup(db):
    acc = account(db)
    db.add(SyncRun(email_account_id=acc.id, status="running"))
    db.commit()
    assert sync_svc.recover_interrupted_runs(db) == 1
    assert db.execute(select(SyncRun)).scalar_one().status == "failed"


def test_exception_mid_apply_rolls_back_that_message_only(db, fixtures, replay, monkeypatch):
    deliver(db, replay, fixtures, "07_globex_se_ack")
    approve_all(db)
    from app.services.pipeline import apply as apply_mod

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(apply_mod, "_side_effects", boom)
    before = _count(db, ApplicationEvent)
    stats = deliver(db, replay, fixtures, "08_globex_oa_dateonly")
    assert stats.failed == 1
    assert _count(db, ApplicationEvent) == before  # no half-written event
    app = db.execute(select(Application)).scalar_one()
    assert app.stage == "acknowledged"


def test_tokens_are_encrypted_at_rest(db):
    from app.core.secrets import load_json_secret, store_secret
    from app.models import Credential

    ref = store_secret(db, "oauth_token", {"access_token": "AT-secret", "refresh_token": "RT-secret"})
    db.commit()
    row = db.get(Credential, ref)
    assert "RT-secret" not in row.ciphertext and "AT-secret" not in row.ciphertext
    assert load_json_secret(db, ref)["refresh_token"] == "RT-secret"
    acc = EmailAccount(provider="gmail", address="x@example.edu", auth_method="oauth_pkce", credential_ref=ref)
    db.add(acc)
    db.commit()
    raw = db.connection().exec_driver_sql("SELECT * FROM email_accounts").fetchall()
    assert "RT-secret" not in repr(raw)
