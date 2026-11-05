"""Local-only access controls shared by all routers."""
from __future__ import annotations

import hmac
import ipaddress

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from ..config import settings
from ..db import get_db
from ..services import settings_service

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "testserver"}


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host in {"localhost", "testclient", "testserver"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    """Reject non-loopback clients, DNS-rebinding Host headers, and cross-site writes."""

    def __init__(self, app, allowed_origins: list[str]) -> None:  # noqa: ANN001
        super().__init__(app)
        self.allowed_origins = set(allowed_origins)

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        if not settings.allow_non_loopback:
            client = request.client.host if request.client else None
            if not _is_loopback(client):
                return JSONResponse({"detail": "AppTracker only accepts local connections."}, status_code=403)
            host = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
            if host not in _LOCAL_HOSTS:
                return JSONResponse({"detail": "Unexpected Host header."}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin not in self.allowed_origins:
                return JSONResponse({"detail": "Cross-origin write blocked."}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        return response


def require_integration_token(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> None:
    expected = settings_service.integration_token(db)
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Missing or invalid integration token.")


def not_found(what: str = "Not found") -> HTTPException:
    return HTTPException(status_code=404, detail=what)
