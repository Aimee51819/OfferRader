"""OAuth 2.0 authorization-code + PKCE for Gmail (gmail.readonly) and Microsoft Graph (Mail.Read).

Only read scopes are ever requested. The consent screen is completed by the
human in their own browser; this module never sees a password. Tokens are
stored encrypted (``core.secrets``) and never logged or returned by the API.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from ...config import settings
from .base import AuthExpired, ProviderUnavailable

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE = "https://oauth2.googleapis.com/revoke"
GOOGLE_SCOPES = ["openid", "email", "https://www.googleapis.com/auth/gmail.readonly"]

MS_SCOPES = ["openid", "email", "offline_access", "https://graph.microsoft.com/Mail.Read"]

# Scopes that must never be requested. Checked before building any auth URL.
FORBIDDEN_SCOPE_FRAGMENTS = (
    "gmail.modify", "gmail.send", "gmail.compose", "gmail.insert", "gmail.labels",
    "mail.google.com", "Mail.ReadWrite", "Mail.Send", "MailboxSettings.ReadWrite",
)


def _ms_base() -> str:
    return f"https://login.microsoftonline.com/{settings.microsoft_tenant}/oauth2/v2.0"


def redirect_uri(provider: str) -> str:
    base = settings.oauth_redirect_base.rstrip("/")
    if provider == "outlook":
        # Microsoft "Mobile and desktop" loopback redirects must use 'localhost'.
        base = base.replace("127.0.0.1", "localhost")
        return f"{base}/api/oauth/microsoft/callback"
    return f"{base}/api/oauth/google/callback"


def _assert_read_only(scopes: list[str]) -> None:
    for s in scopes:
        if any(f.lower() in s.lower() for f in FORBIDDEN_SCOPE_FRAGMENTS):
            raise ValueError(f"Refusing to request non-read-only scope: {s}")


@dataclass
class PendingAuth:
    provider: str
    verifier: str
    created: float
    sync_scope: dict


# state -> PendingAuth. In-memory on purpose: a restart simply requires a new consent click.
_PENDING: dict[str, PendingAuth] = {}
_PENDING_TTL = 600


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def is_configured(provider: str) -> bool:
    if provider == "gmail":
        return bool(settings.google_client_id and settings.google_client_secret)
    if provider == "outlook":
        return bool(settings.microsoft_client_id)
    return False


def start(provider: str, sync_scope: dict | None = None) -> str:
    """Return the consent URL for the human to open."""
    if not is_configured(provider):
        raise ValueError(
            f"{provider} OAuth client is not configured. See README 'OAuth app registration'."
        )
    now = time.time()
    for k in [k for k, v in _PENDING.items() if now - v.created > _PENDING_TTL]:
        _PENDING.pop(k, None)
    state = secrets.token_urlsafe(24)
    verifier, challenge = _pkce()
    _PENDING[state] = PendingAuth(provider, verifier, now, sync_scope or {})
    if provider == "gmail":
        _assert_read_only(GOOGLE_SCOPES)
        params = {
            "client_id": settings.google_client_id,
            "redirect_uri": redirect_uri("gmail"),
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "false",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{GOOGLE_AUTH}?{urlencode(params)}"
    _assert_read_only(MS_SCOPES)
    params = {
        "client_id": settings.microsoft_client_id,
        "redirect_uri": redirect_uri("outlook"),
        "response_type": "code",
        "response_mode": "query",
        "scope": " ".join(MS_SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return f"{_ms_base()}/authorize?{urlencode(params)}"


def take_pending(state: str) -> PendingAuth | None:
    p = _PENDING.pop(state, None)
    if p is None or time.time() - p.created > _PENDING_TTL:
        return None
    return p


def _post_token(url: str, data: dict) -> dict:
    try:
        r = httpx.post(url, data=data, timeout=30)
    except httpx.HTTPError as exc:
        raise ProviderUnavailable(f"token endpoint unreachable: {type(exc).__name__}") from exc
    if r.status_code in (400, 401):
        err = {}
        try:
            err = r.json()
        except ValueError:
            pass
        raise AuthExpired(f"token request rejected: {err.get('error', r.status_code)}")
    if r.status_code >= 500:
        raise ProviderUnavailable(f"token endpoint HTTP {r.status_code}")
    r.raise_for_status()
    return r.json()


def exchange_code(pending: PendingAuth, code: str) -> dict:
    if pending.provider == "gmail":
        tok = _post_token(
            GOOGLE_TOKEN,
            {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "code": code,
                "code_verifier": pending.verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri("gmail"),
            },
        )
    else:
        tok = _post_token(
            f"{_ms_base()}/token",
            {
                "client_id": settings.microsoft_client_id,
                "code": code,
                "code_verifier": pending.verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri("outlook"),
                "scope": " ".join(MS_SCOPES),
            },
        )
    granted = tok.get("scope", "")
    _assert_read_only(granted.split())
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    return tok


def id_token_claims(tok: dict) -> dict:
    """Decode (not verify) the id_token received directly from the token endpoint over TLS."""
    raw = tok.get("id_token")
    if not raw or raw.count(".") != 2:
        return {}
    payload = raw.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        return {}


def refresh(provider: str, tok: dict) -> dict:
    rt = tok.get("refresh_token")
    if not rt:
        raise AuthExpired("no refresh token; reconnect the account")
    if provider == "gmail":
        new = _post_token(
            GOOGLE_TOKEN,
            {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "refresh_token": rt,
                "grant_type": "refresh_token",
            },
        )
    else:
        new = _post_token(
            f"{_ms_base()}/token",
            {
                "client_id": settings.microsoft_client_id,
                "refresh_token": rt,
                "grant_type": "refresh_token",
                "scope": " ".join(MS_SCOPES),
            },
        )
    merged = {**tok, **new}
    merged.setdefault("refresh_token", rt)  # Google usually does not rotate
    merged["expires_at"] = time.time() + int(new.get("expires_in", 3600)) - 60
    return merged


def revoke(provider: str, tok: dict) -> bool:
    """Best-effort revocation. Microsoft has no per-token revoke for public clients."""
    if provider != "gmail":
        return False
    token = tok.get("refresh_token") or tok.get("access_token")
    if not token:
        return False
    try:
        r = httpx.post(GOOGLE_REVOKE, data={"token": token}, timeout=15)
        return r.status_code == 200
    except httpx.HTTPError:
        return False
