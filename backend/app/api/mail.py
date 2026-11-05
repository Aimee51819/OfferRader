"""Email accounts (connect/disconnect), OAuth callbacks, sync control."""
from __future__ import annotations

import html
import threading

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..core.secrets import delete_secret, load_json_secret, store_secret
from ..db import SessionLocal, get_db
from ..models import EmailAccount, EmailMessage, SyncRun, utcnow
from ..schemas import AccountOut, AccountUpdate, ImapAccountIn
from ..services import sync as sync_svc
from ..services.mail import oauth
from ..services.mail.base import MailError
from .dashboard import sync_health
from .deps import not_found

router = APIRouter(prefix="/api", tags=["mail"])


def _account_out(a: EmailAccount) -> dict:
    return {**{c.name: getattr(a, c.name) for c in EmailAccount.__table__.columns}, "connected": bool(a.credential_ref)}


@router.get("/accounts", response_model=list[AccountOut])
def accounts(db: Session = Depends(get_db)) -> list[dict]:
    return [_account_out(a) for a in db.execute(select(EmailAccount).order_by(EmailAccount.created_at)).scalars()]


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(account_id: str, body: AccountUpdate, db: Session = Depends(get_db)) -> dict:
    a = db.get(EmailAccount, account_id)
    if a is None:
        raise not_found("account not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "sync_scope":
            allowed = {"folders", "inbox_only", "include_spam", "lookback_days", "imap_host", "imap_port", "use_ssl"}
            v = {kk: vv for kk, vv in (v or {}).items() if kk in allowed}
            v = {**(a.sync_scope or {}), **v}
        setattr(a, k, v)
    db.commit()
    return _account_out(a)


@router.delete("/accounts/{account_id}", response_model=AccountOut)
def disconnect(account_id: str, db: Session = Depends(get_db)) -> dict:
    """Revoke (where supported) and delete the stored token. Evidence already ingested is kept."""
    a = db.get(EmailAccount, account_id)
    if a is None:
        raise not_found("account not found")
    tok = load_json_secret(db, a.credential_ref)
    if tok and a.provider in {"gmail", "outlook"}:
        oauth.revoke(a.provider, tok)
    delete_secret(db, a.credential_ref)
    a.credential_ref = None
    a.enabled = False
    a.last_error_code = "disconnected"
    db.commit()
    return _account_out(a)


@router.post("/accounts/imap", response_model=AccountOut, status_code=201)
def add_imap(body: ImapAccountIn, db: Session = Depends(get_db)) -> dict:
    ref = store_secret(db, "imap_password", body.password)
    a = EmailAccount(
        provider="imap", address=body.address, auth_method="imap_password", credential_ref=ref, enabled=False,
        sync_scope={"imap_host": body.imap_host, "imap_port": body.imap_port, "use_ssl": body.use_ssl, "folders": body.folders},
    )
    db.add(a)
    db.commit()
    return _account_out(a)


@router.get("/oauth/{provider}/start")
def oauth_start(provider: str) -> dict:
    if provider not in {"gmail", "outlook"}:
        raise not_found("unknown provider")
    if not oauth.is_configured(provider):
        raise HTTPException(
            412,
            "OAuth client is not configured. Add GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET or MICROSOFT_CLIENT_ID "
            "to backend/.env (see README: OAuth app registration), then restart.",
        )
    return {"url": oauth.start(provider), "redirect_uri": oauth.redirect_uri(provider),
            "scopes": oauth.GOOGLE_SCOPES if provider == "gmail" else oauth.MS_SCOPES}


def _callback(provider_key: str, code: str | None, state: str | None, error: str | None, db: Session):
    if error:
        return HTMLResponse(f"<p>Authorization was not completed: {html.escape(error)}. You can close this tab.</p>", 400)
    pending = oauth.take_pending(state or "")
    if pending is None or pending.provider != provider_key or not code:
        return HTMLResponse("<p>This sign-in link expired or is invalid. Start again from Settings.</p>", 400)
    try:
        tok = oauth.exchange_code(pending, code)
    except (MailError, ValueError) as exc:
        return HTMLResponse(f"<p>Sign-in failed ({html.escape(type(exc).__name__)}). Start again from Settings.</p>", 400)
    claims = oauth.id_token_claims(tok)
    address = (claims.get("email") or claims.get("preferred_username") or "").lower()
    if provider_key == "gmail" and not address:
        from ..services.mail import gmail

        prov = gmail.build(lambda: tok["access_token"], lambda: tok["access_token"], {})
        address, _ = prov.address()
    if not address:
        return HTMLResponse("<p>Could not determine the mailbox address.</p>", 400)
    acc = db.execute(
        select(EmailAccount).where(EmailAccount.provider == provider_key, EmailAccount.address == address)
    ).scalar_one_or_none()
    if acc is None:
        acc = EmailAccount(provider=provider_key, address=address, auth_method="oauth_pkce", enabled=True,
                           sync_scope={"folders": ["inbox"]} if provider_key == "outlook" else {})
        db.add(acc)
        db.flush()
    acc.credential_ref = store_secret(db, "oauth_token", tok, ref=acc.credential_ref)
    acc.last_error_code = None
    acc.enabled = True
    db.commit()
    return RedirectResponse(f"/settings?connected={provider_key}")


@router.get("/oauth/google/callback", include_in_schema=False)
def google_callback(code: str | None = None, state: str | None = None, error: str | None = None,
                    db: Session = Depends(get_db)):
    return _callback("gmail", code, state, error, db)


@router.get("/oauth/microsoft/callback", include_in_schema=False)
def microsoft_callback(code: str | None = None, state: str | None = None, error: str | None = None,
                       db: Session = Depends(get_db)):
    return _callback("outlook", code, state, error, db)


# --- sync ----------------------------------------------------------------------------------


@router.get("/sync/status")
def sync_status(db: Session = Depends(get_db)) -> dict:
    from ..scheduler import status as sched_status

    return {**sync_health(db), "running": sync_svc.is_running(), "scheduler": sched_status()}


@router.get("/sync/runs")
def sync_runs(limit: int = Query(20, le=100), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(limit)).scalars().all()
    return [{c.name: getattr(r, c.name) for c in SyncRun.__table__.columns} for r in rows]


@router.post("/sync/run")
def sync_now() -> dict:
    if sync_svc.is_running():
        return {"started": False, "message": "A sync is already running."}
    threading.Thread(target=sync_svc.sync_all, daemon=True, name="manual-sync").start()
    return {"started": True}


@router.post("/sync/dry-run")
def dry_run(limit: int = Query(10, le=50), db: Session = Depends(get_db)) -> dict:
    """Fetch nothing new; show what the pipeline WOULD do for already-stored pending messages."""
    from ..services.pipeline.process import process_message

    backend = sync_svc._backend(db)
    rows = db.execute(
        select(EmailMessage).where(EmailMessage.processing_state.in_(("pending", "failed"))).limit(limit)
    ).scalars().all()
    out = []
    for m in rows:
        try:
            res = process_message(db, m, backend, dry_run=True)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            res = {"state": "error", "error": f"{getattr(exc, 'code', type(exc).__name__)}: {str(exc)[:200]}"}
        out.append({"subject": m.subject, **{k: v for k, v in res.items() if k != "plan"},
                    "events": [e["type"] for e in (res.get("plan") or {}).get("events", [])]})
    db.rollback()
    return {"messages": out, "note": "Dry run: nothing was saved."}


@router.post("/sync/fetch-only")
def fetch_only(account_id: str, db: Session = Depends(get_db)) -> dict:
    """Read new mail into the local store without parsing (first-run check of the connection)."""
    a = db.get(EmailAccount, account_id)
    if a is None:
        raise not_found("account not found")
    run = sync_svc.sync_account(db, a, process=False, max_messages=settings.max_messages_per_sync)
    return {c.name: getattr(run, c.name) for c in SyncRun.__table__.columns}


_ = (SessionLocal, utcnow)
