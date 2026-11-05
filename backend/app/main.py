from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .api import applications, backup, dashboard, integrations, io, mail, reviews, settings, updates
from .api.deps import LocalOnlyMiddleware
from .config import settings as app_settings
from .db import SessionLocal, get_db, init_db
from .ports import APP_ID, server_port
from .scheduler import shutdown as shutdown_scheduler
from .scheduler import start as start_scheduler
from .scheduler import status as scheduler_status
from .services import settings_service
from .version import __version__

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("tracker")

_HERE = Path(__file__).parent
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    STATIC_DIR = Path(sys._MEIPASS) / "frontend" / "dist"
else:
    STATIC_DIR = _HERE.parent.parent / "frontend" / "dist"


def _check_bind() -> None:
    host = app_settings.bind_host
    if host not in {"127.0.0.1", "localhost", "::1"} and not app_settings.allow_non_loopback:
        raise RuntimeError(
            f"BIND_HOST={host} would expose the tracker beyond this Mac. Keep 127.0.0.1, or set "
            "ALLOW_NON_LOOPBACK=true only behind authentication + TLS after a security review."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_bind()
    init_db()
    db = SessionLocal()
    try:
        from .services.sync import recover_interrupted_runs

        n = recover_interrupted_runs(db)
        if n:
            logger.info("Marked %s interrupted sync run(s); they resume from the saved cursor.", n)
        settings_service.integration_token(db)
        interval = int(settings_service.get(db, "poll_interval_seconds"))
    finally:
        db.close()
    if "pytest" not in sys.modules:
        start_scheduler(interval)
    try:
        yield
    finally:
        shutdown_scheduler()


app = FastAPI(
    title="Job Application Feedback Agent",
    version=__version__,
    lifespan=lifespan,
    description="Local-first tracker. Read-only mailbox access; local Qwen via Ollama; no auto-submission.",
)

_port = server_port()
_origins = [
    app_settings.frontend_origin,
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    f"http://localhost:{_port}",
    f"http://127.0.0.1:{_port}",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)
app.add_middleware(LocalOnlyMiddleware, allowed_origins=_origins)

for r in (applications, dashboard, reviews, mail, settings, io, integrations, backup, updates):
    app.include_router(r.router)


@app.get("/api/identity")
def identity() -> dict:
    return {"app": APP_ID, "version": __version__}


@app.get("/api/health")
def health(db: Session = Depends(get_db)) -> dict:
    from .api.dashboard import sync_health
    from .services.llm.ollama import LLMError, default_backend

    try:
        llm = default_backend(
            model=settings_service.get(db, "ollama_model"), base_url=settings_service.get(db, "ollama_base_url")
        ).health()
    except LLMError as exc:
        llm = {"ok": False, "error": str(exc)}
    return {
        "status": "ok",
        "version": __version__,
        "llm": llm,
        "sync": sync_health(db),
        "scheduler": scheduler_status(),
    }


if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    @app.get("/favicon.svg", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "favicon.svg"), media_type="image/svg+xml")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))
