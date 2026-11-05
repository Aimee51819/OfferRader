"""Mailbox sync orchestration: fetch (read-only) -> ingest -> process -> remind.

Crash/sleep safety: the provider cursor is committed in the same transaction
as the page of messages it covers, so a crash either keeps both or neither.
Restarting resumes from the last committed page; re-delivered messages are
deduplicated by (account, provider_message_id).
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.secrets import load_json_secret, load_secret, store_secret
from ..db import SessionLocal
from ..models import EmailAccount, SyncRun, utcnow
from . import settings_service
from .llm.ollama import LLMBackend, default_backend
from .mail import gmail, oauth, outlook
from .mail.base import AuthExpired, MailError, MailProvider, RateLimited
from .mail.imap_client import ImapProvider
from .pipeline.ingest import ingest_message
from .pipeline.process import process_pending

logger = logging.getLogger("tracker.sync")
_LOCK = threading.Lock()

# Test hook: provider factories by account provider name.
PROVIDER_OVERRIDES: dict[str, Callable[[Session, EmailAccount], MailProvider]] = {}


def _token_accessors(db: Session, account: EmailAccount) -> tuple[Callable[[], str], Callable[[], str]]:
    cache: dict = {"tok": load_json_secret(db, account.credential_ref)}
    if not cache["tok"]:
        raise AuthExpired("no stored token; connect the account")

    def refresh() -> str:
        new = oauth.refresh(account.provider, cache["tok"])
        cache["tok"] = new
        store_secret(db, "oauth_token", new, ref=account.credential_ref)
        db.commit()
        return new["access_token"]

    def get() -> str:
        tok = cache["tok"]
        if not tok.get("access_token") or time.time() >= float(tok.get("expires_at", 0)):
            return refresh()
        return tok["access_token"]

    return get, refresh


def provider_for(db: Session, account: EmailAccount) -> MailProvider:
    if account.provider in PROVIDER_OVERRIDES:
        return PROVIDER_OVERRIDES[account.provider](db, account)
    scope = account.sync_scope or {}
    if account.provider == "gmail":
        get, refresh = _token_accessors(db, account)
        return gmail.build(get, refresh, scope)
    if account.provider == "outlook":
        get, refresh = _token_accessors(db, account)
        return outlook.build(get, refresh, scope)
    if account.provider == "imap":
        password = load_secret(db, account.credential_ref)
        if not password:
            raise AuthExpired("no stored IMAP password")
        return ImapProvider(
            scope.get("imap_host", ""),
            int(scope.get("imap_port", 993)),
            account.address,
            password,
            bool(scope.get("use_ssl", True)),
            scope,
        )
    if account.provider == "fake":
        from .mail.fake import FakeProvider, load_fixture_messages

        items = load_fixture_messages(scope["fixture_dir"])
        if scope.get("fixture_account"):
            items = [i for i in items if i.get("account", "primary") == scope["fixture_account"]]
        return FakeProvider(items)
    raise MailError(f"unknown provider {account.provider}")


def recover_interrupted_runs(db: Session) -> int:
    rows = db.execute(select(SyncRun).where(SyncRun.status == "running")).scalars().all()
    for r in rows:
        r.status = "failed"
        r.finished_at = utcnow()
        r.safe_error_summary = "Interrupted (app stopped or Mac slept mid-sync); resumed from last committed page."
    db.commit()
    return len(rows)


def fetch_account(db: Session, account: EmailAccount, run: SyncRun, *, max_messages: int | None = None) -> None:
    max_messages = max_messages or 10_000
    lookback = int((account.sync_scope or {}).get("lookback_days") or settings_service.get(db, "initial_lookback_days"))
    provider = provider_for(db, account)
    for page in provider.iter_pages(account.sync_cursor, lookback_days=lookback, page_size=50):
        for pm in page.messages:
            run.messages_seen += 1
            _row, created = ingest_message(db, account, pm)
            if created:
                run.messages_new += 1
        account.sync_cursor = page.next_cursor
        db.commit()  # messages + cursor together
        if run.messages_seen >= max_messages:
            break


def sync_account(
    db: Session,
    account: EmailAccount,
    *,
    backend: LLMBackend | None = None,
    max_messages: int | None = None,
    process: bool = True,
) -> SyncRun:
    run = SyncRun(email_account_id=account.id, status="running")
    db.add(run)
    account.last_attempt_at = utcnow()
    db.commit()
    status = "success"
    errors: list[str] = []
    try:
        fetch_account(db, account, run, max_messages=max_messages)
        account.last_error_code = None
    except AuthExpired as exc:
        db.rollback()
        status, account.last_error_code = "failed", exc.code
        errors.append("Mailbox authorization expired or was revoked. Reconnect this account in Settings.")
    except RateLimited as exc:
        db.rollback()
        status, account.last_error_code = "partial", exc.code
        errors.append("Provider rate limit reached; will continue from the saved position next sync.")
    except MailError as exc:
        db.rollback()
        status, account.last_error_code = "partial", exc.code
        errors.append(f"Mail provider problem ({exc.code}); progress saved.")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("Unexpected sync failure for account %s", account.id)
        status, account.last_error_code = "failed", "internal_error"
        errors.append(f"Internal error during fetch ({type(exc).__name__}).")

    if process:
        try:
            stats = process_pending(db, backend or _backend(db))
            run.messages_parsed += stats.parsed + stats.ignored
            run.proposals_created += stats.proposals
            run.events_applied += stats.events_applied
            run.error_count += stats.failed
            if stats.aborted_reason:
                status = "partial" if status == "success" else status
                account.last_error_code = account.last_error_code or stats.aborted_reason
                errors.append(
                    "Local model unavailable (" + stats.aborted_reason + "); messages are kept and "
                    "will be parsed on the next run."
                )
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.exception("Processing failed")
            status = "partial"
            errors.append(f"Processing error ({type(exc).__name__}).")

    run = db.get(SyncRun, run.id)
    account = db.get(EmailAccount, account.id)
    run.status = status
    run.finished_at = utcnow()
    run.error_count += len(errors)
    run.safe_error_summary = " ".join(errors)[:1000] or None
    if status == "success":
        account.last_successful_sync_at = run.finished_at
    db.commit()
    return run


def _backend(db: Session) -> LLMBackend:
    return default_backend(
        model=settings_service.get(db, "ollama_model"),
        base_url=settings_service.get(db, "ollama_base_url"),
    )


def sync_all(*, backend: LLMBackend | None = None) -> list[str]:
    """Scheduler entry point. Never runs two syncs at once."""
    if not _LOCK.acquire(blocking=False):
        logger.info("Sync already running; skipping this tick.")
        return []
    db = SessionLocal()
    try:
        ids = []
        accounts = db.execute(select(EmailAccount).where(EmailAccount.enabled.is_(True))).scalars().all()
        for account in accounts:
            run = sync_account(db, account, backend=backend)
            ids.append(run.id)
        from .notifications import evaluate

        evaluate(db)
        return ids
    finally:
        db.close()
        _LOCK.release()


def is_running() -> bool:
    return _LOCK.locked()
