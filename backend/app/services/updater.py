"""In-app updates: check GitHub releases, download, verify, swap on restart.

The app ships as a single unsigned .exe, so there is no installer to hook. The
flow is:

  1. ask the GitHub releases API for the latest tag
  2. download the AppTracker.exe asset plus its .sha256 to a staging folder
  3. verify the hash before anything touches the installed binary
  4. hand the swap to a detached helper script, because Windows keeps the
     running .exe locked and it cannot overwrite itself

Nothing here runs automatically: the user triggers the check and the install.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from ..config import settings as app_settings
from ..ports import APP_ID, port_file
from ..version import GITHUB_REPO, __version__, is_newer

logger = logging.getLogger("tracker.updater")

API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"
ASSET_NAME = "AppTracker.exe"
CHECK_TIMEOUT_S = 15
DOWNLOAD_TIMEOUT_S = 300


# ---------------------------------------------------------------------------
# State — a single download at a time, polled by the UI
# ---------------------------------------------------------------------------

class _State:
    """Deliberately not a dataclass: asdict() would try to deepcopy the lock."""

    def __init__(self) -> None:
        # phase: idle | checking | downloading | verifying | ready | error
        self.phase = "idle"
        self.message = ""
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self.staged_version: str | None = None
        self.error: str | None = None
        self.lock = threading.Lock()

    def snapshot(self) -> dict:
        return {
            "phase": self.phase,
            "message": self.message,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "staged_version": self.staged_version,
            "error": self.error,
            "percent": (
                round(self.downloaded_bytes / self.total_bytes * 100, 1)
                if self.total_bytes
                else 0.0
            ),
            "busy": self.phase in {"checking", "downloading", "verifying"},
        }


_state = _State()


def progress() -> dict:
    return _state.snapshot()


def _set(phase: str, message: str, **kw) -> None:
    with _state.lock:
        _state.phase = phase
        _state.message = message
        for key, value in kw.items():
            setattr(_state, key, value)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def staging_dir() -> Path:
    path = app_settings.data_path / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def installed_exe() -> Path | None:
    """The .exe to replace, or None when running from source."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve()


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------

@dataclass
class UpdateInfo:
    current_version: str
    latest_version: str | None = None
    update_available: bool = False
    release_url: str = RELEASES_URL
    notes: str = ""
    published_at: str | None = None
    asset_url: str | None = None
    asset_size: int = 0
    sha_url: str | None = None
    supported: bool = True
    reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _get_json(url: str, timeout: int) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"AppTracker/{__version__}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def unsupported_reason() -> str | None:
    """Why in-app updating can't work here, or None when it can."""
    if installed_exe() is None:
        return "Running from source — update with `git pull` instead of the installer."
    if platform.system() != "Windows":
        return "Automatic updates are only wired up for the Windows build."
    return None


def local_status() -> UpdateInfo:
    """What we know without touching the network."""
    info = UpdateInfo(current_version=__version__)
    info.reason = unsupported_reason()
    info.supported = info.reason is None
    return info


def check_for_update() -> UpdateInfo:
    """Ask GitHub for the newest release. Never raises — the UI shows `reason`."""
    info = local_status()
    if not info.supported:
        return info

    _set("checking", "Checking for updates…", error=None)
    try:
        release = _get_json(API_URL, CHECK_TIMEOUT_S)
    except urllib.error.HTTPError as exc:
        reason = (
            "No releases published yet."
            if exc.code == 404
            else f"GitHub returned HTTP {exc.code}."
        )
        info.reason = reason
        _set("error", reason, error=reason)
        return info
    except Exception as exc:  # noqa: BLE001 - offline is normal, not exceptional
        reason = f"Could not reach GitHub ({exc})."
        info.reason = reason
        _set("error", reason, error=reason)
        return info

    tag = (release.get("tag_name") or "").strip()
    info.latest_version = tag.lstrip("vV") or None
    info.notes = (release.get("body") or "").strip()
    info.published_at = release.get("published_at")
    info.release_url = release.get("html_url") or RELEASES_URL

    for asset in release.get("assets") or []:
        name = asset.get("name") or ""
        if name == ASSET_NAME:
            info.asset_url = asset.get("browser_download_url")
            info.asset_size = int(asset.get("size") or 0)
        elif name == f"{ASSET_NAME}.sha256":
            info.sha_url = asset.get("browser_download_url")

    if not tag:
        info.reason = "The latest release has no version tag."
    elif not is_newer(tag):
        info.reason = f"You are on the latest version ({__version__})."
    elif info.asset_url is None:
        info.reason = f"Release {tag} does not include {ASSET_NAME}."
    else:
        info.update_available = True

    _set("idle", info.reason or f"Update {tag} available", error=None)
    return info


# ---------------------------------------------------------------------------
# Downloading + verifying
# ---------------------------------------------------------------------------

def _download(url: str, dest: Path, *, expected_size: int = 0) -> None:
    request = urllib.request.Request(
        url, headers={"User-Agent": f"AppTracker/{__version__}"}
    )
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as resp:
        total = int(resp.headers.get("Content-Length") or expected_size or 0)
        _set("downloading", "Downloading update…", downloaded_bytes=0, total_bytes=total)
        done = 0
        with dest.open("wb") as handle:
            while chunk := resp.read(256 * 1024):
                handle.write(chunk)
                done += len(chunk)
                with _state.lock:
                    _state.downloaded_bytes = done


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_hash(sha_url: str) -> str | None:
    """Read the published checksum. Format: "<hex>  AppTracker.exe"."""
    try:
        request = urllib.request.Request(
            sha_url, headers={"User-Agent": f"AppTracker/{__version__}"}
        )
        with urllib.request.urlopen(request, timeout=CHECK_TIMEOUT_S) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch checksum from %s: %s", sha_url, exc)
        return None
    token = text.strip().split()[0].lower() if text.strip() else ""
    return token if len(token) == 64 else None


def staged_path(version: str) -> Path:
    return staging_dir() / f"AppTracker-{version}.exe"


def download_update(info: UpdateInfo) -> Path:
    """Fetch and hash-verify the new exe. Returns the staged file path."""
    if not info.update_available or not info.asset_url or not info.latest_version:
        raise RuntimeError(info.reason or "No update available to download.")

    target = staged_path(info.latest_version)
    partial = target.with_suffix(".exe.part")
    partial.unlink(missing_ok=True)

    _download(info.asset_url, partial, expected_size=info.asset_size)

    _set("verifying", "Verifying download…")
    if info.sha_url:
        expected = _expected_hash(info.sha_url)
        if expected:
            actual = _sha256(partial)
            if actual != expected:
                partial.unlink(missing_ok=True)
                raise RuntimeError(
                    "Downloaded file failed its checksum — the update was discarded."
                )
        else:
            logger.warning("Release published no usable checksum; skipping verification.")

    partial.replace(target)
    for old in staging_dir().glob("AppTracker-*.exe"):
        if old != target:
            old.unlink(missing_ok=True)

    _set(
        "ready",
        f"Version {info.latest_version} is ready — restart to finish installing.",
        staged_version=info.latest_version,
    )
    return target


def find_staged() -> tuple[str, Path] | None:
    """A verified download waiting to be installed, if any."""
    newest: tuple[str, Path] | None = None
    for path in staging_dir().glob("AppTracker-*.exe"):
        version = path.stem.removeprefix("AppTracker-")
        if not is_newer(version):
            path.unlink(missing_ok=True)
            continue
        if newest is None or is_newer(version, newest[0]):
            newest = (version, path)
    return newest


def start_download(info: UpdateInfo) -> None:
    """Run download_update on a worker so the request returns immediately."""

    def run() -> None:
        try:
            download_update(info)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Update download failed")
            _set("error", str(exc), error=str(exc))

    threading.Thread(target=run, name="updater", daemon=True).start()


# ---------------------------------------------------------------------------
# Applying — hand the swap to a helper that outlives this process
# ---------------------------------------------------------------------------

_SWAP_PS1 = r"""$ErrorActionPreference = 'Continue'
$log    = '{log}'
$staged = '{staged}'
$target = '{target}'
$portFile = '{port_file}'

function Note($m) {{ Add-Content -LiteralPath $log -Value ((Get-Date -Format s) + ' ' + $m) }}
Note "swap helper started: $staged -> $target"

# Windows holds a lock on the running .exe, so retry the move until AppTracker
# has exited rather than polling for its pid (a detached helper has no console,
# which makes tasklist/find unusable).
$moved = $false
for ($i = 0; $i -lt 120; $i++) {{
    try {{
        Move-Item -LiteralPath $staged -Destination $target -Force -ErrorAction Stop
        $moved = $true
        break
    }} catch {{
        Start-Sleep -Milliseconds 500
    }}
}}

function Healthy {{
    try {{
        # The relaunch may land on any free port, so follow the port it
        # records once healthy, and make sure AppTracker is what answers there.
        if (-not (Test-Path -LiteralPath $portFile)) {{ return $false }}
        $port = (Get-Content -LiteralPath $portFile -Raw).Trim()
        if ($port -notmatch '^[0-9]+$') {{ return $false }}
        $r = Invoke-WebRequest -Uri ("http://127.0.0.1:" + $port + "/api/identity") -TimeoutSec 3 -UseBasicParsing
        return ($r.StatusCode -eq 200) -and ($r.Content -match '"app"\s*:\s*"{app_id}"')
    }} catch {{ return $false }}
}}

if (-not $moved) {{
    Note "giving up; left the staged file for the next launch to retry"
}} else {{
    Note "replaced the binary; relaunching"
    # Anything left in the port file belongs to the build we just replaced.
    Remove-Item -LiteralPath $portFile -Force -ErrorAction SilentlyContinue
    # Do not exit until the new instance answers. Quitting straight after
    # Start-Process lets the child die with this helper, and the old port can
    # still be held by the process we just replaced.
    $up = $false
    for ($attempt = 1; $attempt -le 3 -and -not $up; $attempt++) {{
        try {{
            $p = Start-Process -FilePath $target -ArgumentList {launch_args} -PassThru
            Note ("attempt " + $attempt + ": started pid=" + $p.Id)
        }} catch {{
            Note ("attempt " + $attempt + " could not start: " + $_)
            Start-Sleep -Seconds 3
            continue
        }}
        for ($i = 0; $i -lt 40; $i++) {{
            Start-Sleep -Milliseconds 750
            if (Healthy) {{ $up = $true; break }}
        }}
        if (-not $up) {{ Note ("attempt " + $attempt + " never became healthy") }}
    }}
    if ($up) {{ Note "new version is up" }} else {{ Note "relaunch failed" }}
}}
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
"""


def _clean_env() -> dict[str, str]:
    """Environment for the helper, stripped of PyInstaller's bootloader state.

    The one-file bootloader hands its child a set of _PYI_* / _MEIPASS2
    variables pointing at the temp directory it unpacked into. Those get
    inherited all the way down to the relaunched AppTracker, whose bootloader
    then trusts them and looks for an unpack directory that no longer exists —
    it starts and silently wedges before Python ever runs.
    """
    def inherited(key: str) -> bool:
        return not key.startswith("_PYI") and not key.startswith("_MEIPASS")

    dropped = [k for k in os.environ if not inherited(k)]
    if dropped:
        logger.info("Stripping bootloader vars from the helper env: %s", dropped)
    return {k: v for k, v in os.environ.items() if inherited(k)}


def apply_staged_update(port: int, *, staged: Path | None = None) -> bool:
    """Spawn the helper that replaces the installed exe once we exit.

    `port` is the preferred port the new instance is launched with; it may fall
    back to another, so the helper finds it through the launcher's port file.

    Returns True when the helper was launched; the caller must then shut down
    promptly, because the helper is already waiting on this PID.
    """
    target = installed_exe()
    if target is None:
        logger.info("Not a packaged build — nothing to swap.")
        return False

    if staged is None:
        found = find_staged()
        if found is None:
            return False
        staged = found[1]
    if not staged.exists():
        return False

    if platform.system() != "Windows":
        # POSIX lets us replace a running binary in place.
        try:
            shutil.copy2(staged, target)
            target.chmod(0o755)
            staged.unlink(missing_ok=True)
            logger.info("Update applied in place; restart to run it.")
            return True
        except OSError as exc:
            logger.error("Could not apply update: %s", exc)
            return False

    script = staging_dir() / "apply-update.ps1"
    script.write_text(
        _SWAP_PS1.format(
            log=str(staging_dir() / "apply-update.log"),
            staged=str(staged),
            target=str(target),
            launch_args=f"'--port','{port}'",
            port_file=str(port_file(app_settings.data_path)),
            app_id=APP_ID,
        ),
        encoding="utf-8",
    )

    # CREATE_NO_WINDOW keeps the console hidden; CREATE_NEW_PROCESS_GROUP stops
    # our own Ctrl+C / shutdown from reaching the helper.
    creation_flags = 0x08000000 | 0x00000200
    try:
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden",
                "-File", str(script),
            ],
            creationflags=creation_flags,
            close_fds=True,
            cwd=str(staging_dir()),
            env=_clean_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        logger.error("Could not start the update helper: %s", exc)
        return False

    logger.info("Update helper started; exiting so it can replace %s", target)
    return True


# ---------------------------------------------------------------------------
# Shutdown hook — the launcher owns the tray and the server, not the API
# ---------------------------------------------------------------------------

_quit_hook: Callable[[], None] | None = None


def register_quit_hook(fn: Callable[[], None]) -> None:
    """Let the launcher supply an orderly shutdown (stop server, drop tray)."""
    global _quit_hook
    _quit_hook = fn


def restart_for_update(port: int) -> bool:
    """Stage-swap and quit. The helper relaunches us on the new binary."""
    if not apply_staged_update(port):
        return False

    def shutdown() -> None:
        time.sleep(1.0)  # let the HTTP response flush first
        if _quit_hook is not None:
            try:
                _quit_hook()
                return
            except Exception:  # noqa: BLE001
                logger.exception("Quit hook failed; forcing exit")
        os._exit(0)

    threading.Thread(target=shutdown, name="updater-quit", daemon=True).start()
    return True
