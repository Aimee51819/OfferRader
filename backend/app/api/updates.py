from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..ports import preferred_port
from ..services import updater

router = APIRouter(prefix="/api/updates", tags=["updates"])


class UpdateStatus(BaseModel):
    current_version: str
    latest_version: str | None = None
    update_available: bool = False
    release_url: str
    notes: str = ""
    published_at: str | None = None
    supported: bool = True
    reason: str | None = None
    staged_version: str | None = None
    phase: str = "idle"
    message: str = ""
    percent: float = 0.0
    busy: bool = False
    error: str | None = None


def _status(info: updater.UpdateInfo) -> UpdateStatus:
    progress = updater.progress()
    staged = updater.find_staged()
    return UpdateStatus(
        **{
            k: v
            for k, v in info.to_dict().items()
            if k not in {"asset_url", "asset_size", "sha_url"}
        },
        staged_version=staged[0] if staged else None,
        phase=progress["phase"],
        message=progress["message"],
        percent=progress["percent"],
        busy=progress["busy"],
        error=progress["error"],
    )


@router.get("", response_model=UpdateStatus)
def current_status() -> UpdateStatus:
    """Cheap, offline: what we know without calling GitHub."""
    return _status(updater.local_status())


@router.post("/check", response_model=UpdateStatus)
def check() -> UpdateStatus:
    return _status(updater.check_for_update())


@router.post("/download", response_model=UpdateStatus)
def download() -> UpdateStatus:
    if updater.progress()["busy"]:
        raise HTTPException(409, "An update is already downloading.")
    info = updater.check_for_update()
    if not info.update_available:
        raise HTTPException(400, info.reason or "No update available.")
    updater.start_download(info)
    return _status(info)


@router.post("/install", response_model=dict)
def install() -> dict:
    """Swap in the staged build and restart. The response lands before we exit."""
    staged = updater.find_staged()
    if staged is None:
        raise HTTPException(400, "No verified download is waiting to be installed.")
    # Relaunch from the preferred port so a busy-port fallback stays a one-off.
    if not updater.restart_for_update(preferred_port()):
        raise HTTPException(
            400,
            "Could not start the update helper. Install manually from the "
            "release page.",
        )
    return {
        "ok": True,
        "version": staged[0],
        "message": "AppTracker is restarting to finish the update.",
    }
