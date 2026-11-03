from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from .api import (
    analytics,
    applications,
    backup,
    emails,
    parse,
    settings,
    suggestions,
    updates,
)
from .config import settings as app_settings
from .db import SessionLocal, get_db, init_db
from .models import EmailAccount
from .ports import APP_ID, server_port
from .scheduler import shutdown as shutdown_scheduler
from .scheduler import start as start_scheduler
from .services import sync_progress
from .services.llm.service import get_active_provider
from .services.settings_service import get_poll_interval
from .version import __version__

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tracker")

# Resolve the built frontend directory relative to this file so it works
# regardless of where the process is launched from. PyInstaller unpacks
# datas into sys._MEIPASS.
_HERE = Path(__file__).parent
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    STATIC_DIR = Path(sys._MEIPASS) / "frontend" / "dist"
else:
    STATIC_DIR = _HERE.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        interval = get_poll_interval(db, app_settings.email_poll_interval_seconds)
    finally:
        db.close()
    start_scheduler(interval)
    try:
        yield
    finally:
        shutdown_scheduler()


app = FastAPI(title="Job Application Tracker", version=__version__, lifespan=lifespan)

# The launcher may land on a port other than 8000 when it is busy.
_port = server_port()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        app_settings.frontend_origin,
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        f"http://localhost:{_port}",
        f"http://127.0.0.1:{_port}",
    ],
    # No extension origins: the capture extension's host_permissions already
    # exempt it from CORS, and allowing chrome-extension://* would let every
    # installed extension read this API.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(applications.router)
app.include_router(analytics.router)
app.include_router(parse.router)
app.include_router(emails.router)
app.include_router(suggestions.router)
app.include_router(settings.router)
app.include_router(backup.router)
app.include_router(updates.router)


@app.get("/api/identity")
def identity() -> dict:
    """Cheap fingerprint for port probing: no DB access, always the same shape."""
    return {"app": APP_ID, "version": __version__}


@app.get("/api/health")
def health(db: Session = Depends(get_db)) -> dict:
    try:
        provider = get_active_provider(db)
        accounts = list(db.execute(select(EmailAccount)).scalars().all())
        active_accounts = [a for a in accounts if a.active]
        last_synced = max(
            (a.last_synced_at for a in accounts if a.last_synced_at),
            default=None,
        )
        llm = {
            "configured": provider is not None,
            "name": provider.name if provider else None,
            "provider": provider.provider if provider else None,
            "model": provider.model if provider else None,
        }
        email = {
            "accounts": len(accounts),
            "active": len(active_accounts),
            "last_synced_at": last_synced.isoformat() if last_synced else None,
        }
        # Release any open transaction immediately — health is polled often.
        db.commit()
    except Exception as exc:  # noqa: BLE001 - never let health hang/crash startup
        logger.warning("Health DB check failed: %s", exc)
        llm = {"configured": False, "name": None, "provider": None, "model": None}
        email = {"accounts": 0, "active": 0, "last_synced_at": None, "error": str(exc)}

    return {
        "status": "ok",
        "version": __version__,
        "llm": llm,
        "email": email,
        "sync": sync_progress.get(),
    }


# Serve the built React app if frontend/dist exists.
# In dev mode (npm run dev) this is skipped and Vite handles the frontend.
if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    @app.get("/favicon.svg", include_in_schema=False)
    async def favicon() -> FileResponse:
        """Served explicitly: the SPA fallback below would return index.html."""
        return FileResponse(str(STATIC_DIR / "favicon.svg"), media_type="image/svg+xml")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        """Return index.html for any non-API path so client-side routing works."""
        index = STATIC_DIR / "index.html"
        return FileResponse(str(index))
