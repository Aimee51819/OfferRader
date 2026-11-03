"""
Job Application Tracker — system tray launcher.

Usage:
    python launcher.py            # port 8000, or the next free one if busy
    python launcher.py --port 9000  # prefer 9000 instead

What it does:
  1. Builds the frontend if frontend/dist is missing.
  2. Starts the FastAPI/uvicorn server in a background thread.
  3. Shows a tray icon with Open / Start on Login / Quit options.
"""
from __future__ import annotations

import argparse
import logging
import os
import platform
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn

if TYPE_CHECKING:
    from app.instance_lock import InstanceLock

logger = logging.getLogger("tracker.launcher")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE = Path(__file__).parent          # backend/
ROOT = HERE.parent                    # project root
FRONTEND_DIST = ROOT / "frontend" / "dist"
FRONTEND_DIR = ROOT / "frontend"

# Make the backend package importable before anything below needs app.config.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def _data_dir() -> Path:
    """Per-user data directory, falling back to the repo if config won't load."""
    try:
        from app.config import settings as app_settings

        return app_settings.data_path
    except Exception:  # noqa: BLE001 - logging must work even if config fails
        fallback = HERE / "data"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


LOG_DIR = _data_dir()
LOG_FILE = LOG_DIR / "launcher.log"
FIRST_RUN_MARKER = LOG_DIR / ".welcomed"
INSTANCE_LOCK_FILE = LOG_DIR / "apptracker.lock"

# Windows Startup entries
_WIN_STARTUP = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"
_WIN_STARTUP_BAT = _WIN_STARTUP / "AppTracker.bat"
_WIN_STARTUP_VBS = _WIN_STARTUP / "AppTracker.vbs"


# ---------------------------------------------------------------------------
# Optional deps — pystray / Pillow are only needed for the tray icon.
# ---------------------------------------------------------------------------
try:
    from PIL import Image, ImageDraw
    import pystray
    _TRAY_AVAILABLE = True
except Exception:  # noqa: BLE001 - pystray raises non-ImportErrors (e.g. Xlib) without a display
    _TRAY_AVAILABLE = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _configure_logging() -> None:
    """Log to console when available, and always to a file (pythonw has no console)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)

    # Avoid duplicate StreamHandlers if main() is re-entered.
    if sys.stderr is not None and not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    ):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)


def _build_frontend() -> None:
    """Run `npm run build` inside frontend/ if dist is absent."""
    # A packaged build ships the UI inside the bundle and has no npm or source
    # tree to build from; app.main serves it out of sys._MEIPASS.
    if getattr(sys, "frozen", False):
        return
    if FRONTEND_DIST.is_dir():
        return
    logger.info("frontend/dist not found — building now (this takes ~10s)…")
    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=str(FRONTEND_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("Frontend build failed:\n%s", result.stderr)
        sys.exit(1)
    logger.info("Frontend built successfully.")


def _icon_path() -> Path | None:
    """Bundled icon, whether running from source or a PyInstaller build."""
    candidates = [HERE / "assets" / "icon.ico"]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.insert(0, Path(sys._MEIPASS) / "assets" / "icon.ico")
    return next((p for p in candidates if p.exists()), None)


def _make_tray_icon() -> "Image.Image":
    """The shared app mark, or a plain fallback if the asset is missing."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    path = _icon_path()
    if path is not None:
        try:
            return Image.open(path).convert("RGBA")
        except Exception:  # noqa: BLE001 - the tray must still appear
            logger.warning("Could not load %s; using fallback icon.", path)

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([2, 2, 62, 62], radius=14, fill=(26, 25, 23, 255))
    for left, top, color in ((14, 37, (233, 229, 222, 255)),
                             (29, 26, (233, 229, 222, 255)),
                             (44, 14, (200, 115, 74, 255))):
        draw.rounded_rectangle([left, top, left + 8, 50], radius=3, fill=color)
    return img


def _is_apptracker(url: str, timeout_s: float = 1.0) -> bool:
    """True when the server at `url` identifies itself as AppTracker."""
    import json
    import urllib.request

    from app.ports import APP_ID

    try:
        with urllib.request.urlopen(f"{url}/api/identity", timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8")).get("app") == APP_ID
    except Exception:  # noqa: BLE001
        return False


def _find_running_instance(start: int) -> str | None:
    """URL of an AppTracker already serving in the scan range, if any.

    A previous launch may have fallen back past `start`, so look at every busy
    port in the range rather than just the preferred one. Free ports are
    skipped without any network traffic.
    """
    from app.ports import PORT_SCAN_SPAN, is_port_free

    for port in range(start, start + PORT_SCAN_SPAN + 1):
        if is_port_free(port):
            continue
        url = f"http://127.0.0.1:{port}"
        if _is_apptracker(url):
            return url
    return None


def _wait_for_other_launch(start: int, lock: "InstanceLock", timeout_s: float) -> str | None:
    """Wait on the launch that holds the instance lock.

    Returns its URL once it serves. Returns None if it exits first (and we
    took the lock over, see `lock.held`) or if it is still not answering
    when the timeout runs out.
    """
    deadline = time.time() + timeout_s
    while True:
        running = _find_running_instance(start)
        if running:
            return running
        if lock.acquire() or time.time() >= deadline:
            return None
        time.sleep(1.0)


def _alert(title: str, message: str) -> None:
    """Show an error even from a windowed build, which has no console."""
    logger.error("%s: %s", title, message)
    if platform.system() == "Windows":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # MB_ICONERROR
        except Exception:  # noqa: BLE001 - the log line above still stands
            pass


def _wait_for_health(
    url: str, timeout_s: float = 15.0, server: "_ServerThread | None" = None
) -> bool:
    """Wait for our server to answer. Gives up early if its thread has died."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _is_apptracker(url):
            return True
        if server is not None and not server.is_alive():
            return False
        time.sleep(0.2)
    return False


def _is_startup_enabled() -> bool:
    system = platform.system()
    if system == "Windows":
        return _WIN_STARTUP_VBS.exists() or _WIN_STARTUP_BAT.exists()
    if system == "Darwin":
        plist = Path.home() / "Library/LaunchAgents/com.apptracker.launcher.plist"
        return plist.exists()
    # Linux — systemd user service
    service = Path.home() / ".config/systemd/user/apptracker.service"
    return service.exists()


def _enable_startup(port: int) -> None:
    system = platform.system()
    python = sys.executable
    script = str(HERE / "launcher.py")

    if system == "Windows":
        # Prefer pythonw.exe — no console window.
        pythonw = Path(python).parent / "pythonw.exe"
        exe = str(pythonw) if pythonw.exists() else python
        work_dir = str(HERE)

        # VBScript launches hidden (window style 0) so no cmd flash on login.
        # Delay a few seconds so OneDrive / network paths are ready.
        vbs = (
            'Set sh = CreateObject("WScript.Shell")\r\n'
            f'sh.CurrentDirectory = "{work_dir}"\r\n'
            "WScript.Sleep 8000\r\n"
            f'sh.Run """{exe}"" ""{script}"" --port {port}", 0, False\r\n'
        )
        _WIN_STARTUP.mkdir(parents=True, exist_ok=True)
        _WIN_STARTUP_VBS.write_text(vbs, encoding="utf-8")
        # Remove the old .bat that flashed a console window.
        _WIN_STARTUP_BAT.unlink(missing_ok=True)
        logger.info("Start-on-login enabled (Windows Startup VBScript, silent).")

    elif system == "Darwin":
        agents = Path.home() / "Library/LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        plist = agents / "com.apptracker.launcher.plist"
        plist.write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.apptracker.launcher</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{script}</string>
    <string>--port</string><string>{port}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
</dict>
</plist>
""",
            encoding="utf-8",
        )
        subprocess.run(["launchctl", "load", str(plist)], check=False)
        logger.info("Start-on-login enabled (macOS LaunchAgent).")

    else:
        service_dir = Path.home() / ".config/systemd/user"
        service_dir.mkdir(parents=True, exist_ok=True)
        service = service_dir / "apptracker.service"
        service.write_text(
            f"""[Unit]
Description=Job Application Tracker

[Service]
ExecStart={python} {script} --port {port}
Restart=on-failure

[Install]
WantedBy=default.target
""",
            encoding="utf-8",
        )
        subprocess.run(["systemctl", "--user", "enable", "apptracker"], check=False)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        logger.info("Start-on-login enabled (systemd user service).")


def _disable_startup() -> None:
    system = platform.system()
    if system == "Windows":
        _WIN_STARTUP_BAT.unlink(missing_ok=True)
        _WIN_STARTUP_VBS.unlink(missing_ok=True)
    elif system == "Darwin":
        plist = Path.home() / "Library/LaunchAgents/com.apptracker.launcher.plist"
        subprocess.run(["launchctl", "unload", str(plist)], check=False)
        plist.unlink(missing_ok=True)
    else:
        subprocess.run(["systemctl", "--user", "disable", "apptracker"], check=False)
        (Path.home() / ".config/systemd/user/apptracker.service").unlink(missing_ok=True)
    logger.info("Start-on-login disabled.")


def _apply_pending_update(port: int) -> bool:
    """Install a download staged by a previous session, then hand off and exit.

    Windows keeps the running .exe locked, so even at startup we cannot
    overwrite ourselves — a detached helper waits for this process to exit,
    swaps the binary and relaunches it. True means "we are handing off".
    """
    if not getattr(sys, "frozen", False):
        return False
    try:
        from app.services import updater
    except Exception:  # noqa: BLE001 - never block startup on the updater
        logger.exception("Could not load the updater")
        return False

    staged = updater.find_staged()
    if staged is None:
        return False
    version, path = staged

    # A swap that keeps failing must not turn into a relaunch loop that never
    # reaches the tray. Give up on the staged file after a few tries.
    attempts_file = path.with_suffix(".attempts")
    attempts = 0
    try:
        attempts = int(attempts_file.read_text(encoding="ascii").strip() or 0)
    except (OSError, ValueError):
        pass
    if attempts >= 3:
        logger.error(
            "Giving up on staged update %s after %s attempts; discarding it.",
            version, attempts,
        )
        path.unlink(missing_ok=True)
        attempts_file.unlink(missing_ok=True)
        return False
    try:
        attempts_file.write_text(str(attempts + 1), encoding="ascii")
    except OSError:
        pass

    logger.info("Installing staged update %s…", version)
    if updater.apply_staged_update(port, staged=path):
        return True
    attempts_file.unlink(missing_ok=True)
    return False


def _migrate_windows_startup_if_needed(port: int) -> None:
    """Replace legacy AppTracker.bat (console flash) with silent .vbs."""
    if platform.system() != "Windows":
        return
    if _WIN_STARTUP_BAT.exists() and not _WIN_STARTUP_VBS.exists():
        logger.info("Migrating Start-on-Login from .bat to silent .vbs…")
        _enable_startup(port)


# ---------------------------------------------------------------------------
# Server thread
# ---------------------------------------------------------------------------

class _ServerThread(threading.Thread):
    def __init__(self, port: int) -> None:
        super().__init__(daemon=True, name="uvicorn")
        self.port = port
        self._server: uvicorn.Server | None = None
        self.failed = False
        self.error: str | None = None

    def run(self) -> None:
        try:
            # Ensure the backend package is importable when launched from anywhere.
            if str(HERE) not in sys.path:
                sys.path.insert(0, str(HERE))

            # Always run with a known working directory (Startup may start elsewhere).
            os.chdir(HERE)

            # DATA_DIR is intentionally left alone: app.config resolves it to a
            # per-user directory (%LOCALAPPDATA%\AppTracker) and migrates an
            # older repo-local database on first run.

            # pythonw has sys.stdout=None; uvicorn's default ColorFormatter
            # crashes on stdout.isatty() unless use_colors is forced off.
            # log_config=None so uvicorn propagates to the root logger instead
            # of installing its own stderr handlers. A windowed build has no
            # stderr, which previously made 500s and tracebacks invisible.
            config = uvicorn.Config(
                "app.main:app",
                host="127.0.0.1",
                port=self.port,
                log_level="info",
                use_colors=False,
                log_config=None,
            )
            self._server = uvicorn.Server(config)
            self._server.run()
        except Exception:
            self.failed = True
            self.error = traceback.format_exc()
            logger.exception("Uvicorn server crashed")

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True


def _write_port_file(port: int) -> None:
    """Tell the update helper where we landed (see app.ports.port_file)."""
    from app.ports import port_file

    try:
        port_file(LOG_DIR).write_text(str(port), encoding="ascii")
    except OSError:
        logger.warning("Could not record the server port in %s", port_file(LOG_DIR))


def _start_server(start: int) -> tuple[_ServerThread | None, int | None, bool]:
    """Start uvicorn on the first free port from `start`. Returns (server, port, healthy).

    The port is chosen right before uvicorn binds. If another program still
    grabs it in between, uvicorn exits its thread on the bind error and we move
    on to the next free port.
    """
    from app.ports import PORT_SCAN_SPAN, find_free_port, is_port_free

    end = start + PORT_SCAN_SPAN
    port = find_free_port(start, end)
    for _attempt in range(3):
        if port is None:
            break
        # app.main reads this for CORS, and the updater to relaunch here.
        os.environ["APPTRACKER_PORT"] = str(port)
        if port != start:
            logger.info("Port %s is busy; using port %s instead.", start, port)
        logger.info("Starting AppTracker server on http://127.0.0.1:%s", port)
        server = _ServerThread(port)
        server.start()
        healthy = _wait_for_health(f"http://127.0.0.1:{port}", timeout_s=20.0, server=server)
        if healthy or server.is_alive() or is_port_free(port):
            # Up, still starting, or crashed for a reason unrelated to the port.
            if healthy:
                _write_port_file(port)
            return server, port, healthy
        logger.warning("Port %s was taken before the server could bind; retrying.", port)
        port = find_free_port(port + 1, end)

    logger.error(
        "No free port between %s and %s. Close another program or start with "
        "--port <number>.",
        start,
        end,
    )
    return None, None, False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    _configure_logging()

    parser = argparse.ArgumentParser(description="Job Application Tracker launcher")
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Preferred port; if it is busy the next free one (up to +100) is used",
    )
    parser.add_argument("--no-tray", action="store_true", help="Run without a tray icon (headless)")
    args = parser.parse_args()

    # The preferred port, not the one we land on, is what Start on Login and
    # the update handoff should remember: a fallback is a one-off.
    preferred_port = args.port
    os.environ["APPTRACKER_PREFERRED_PORT"] = str(preferred_port)

    # Only one launcher per data directory: a second one starting while the
    # first is still building or booting would otherwise fall back to the next
    # port and run a second server and email scheduler on the same database.
    # Held until this process exits.
    from app.instance_lock import InstanceLock

    instance_lock = InstanceLock(INSTANCE_LOCK_FILE)
    if not instance_lock.acquire():
        running = _wait_for_other_launch(preferred_port, instance_lock, timeout_s=60.0)
        if running:
            logger.info("AppTracker is already running at %s — opening it.", running)
            webbrowser.open(running)
            return
        if not instance_lock.held:
            _alert(
                "AppTracker is already running",
                "Another copy of AppTracker is running but not responding. Quit it "
                f"from its tray icon, then start AppTracker again.\n\nLog: {LOG_FILE}",
            )
            return
        logger.info("The other AppTracker exited; starting normally.")

    # Starting twice used to die on "address already in use" and leave a tray
    # icon that reported a server it didn't own. Hand off to the live one.
    # This also catches an older build that predates the instance lock.
    running = _find_running_instance(preferred_port)
    if running:
        logger.info("AppTracker is already running at %s — opening it.", running)
        webbrowser.open(running)
        return

    if _apply_pending_update(preferred_port):
        return

    _migrate_windows_startup_if_needed(preferred_port)
    _build_frontend()

    server, port, healthy = _start_server(preferred_port)
    if server is None or port is None:
        return
    url = f"http://127.0.0.1:{port}"

    if not healthy:
        logger.error(
            "Server did not become healthy at %s. See log: %s",
            url,
            LOG_FILE,
        )
        if server.error:
            logger.error("Server thread error:\n%s", server.error)
    elif not FIRST_RUN_MARKER.exists() or port != preferred_port:
        # Subsequent launches stay silent in the tray; the first one needs to
        # show the user something happened, and so does a move to a new port,
        # or a bookmark to the old one would reach some other program.
        try:
            FIRST_RUN_MARKER.write_text("opened\n", encoding="utf-8")
        except OSError:
            pass
        webbrowser.open(url)

    logger.info("AppTracker is serving on port %s: %s", port, url)

    if not _TRAY_AVAILABLE or args.no_tray:
        if healthy:
            logger.info("App running at %s  (Ctrl+C to quit)", url)
        try:
            server.join()
        except KeyboardInterrupt:
            server.stop()
        return

    # ---- tray icon ----
    icon_image = _make_tray_icon()

    def on_open(_icon, _item) -> None:
        if _wait_for_health(url, timeout_s=3.0):
            webbrowser.open(url)
        else:
            logger.error(
                "Cannot open app — server not responding at %s. See %s",
                url,
                LOG_FILE,
            )

    def on_toggle_startup(_icon, _item) -> None:
        if _is_startup_enabled():
            _disable_startup()
        else:
            _enable_startup(preferred_port)

    # Set when we stop the server on purpose, so the watchdog stays quiet.
    quitting = threading.Event()

    def on_quit(_icon, _item) -> None:
        quitting.set()
        server.stop()
        _icon.stop()

    def on_check_updates(_icon, _item) -> None:
        try:
            from app.services import updater as _u

            info = _u.check_for_update()
            if info.update_available:
                _icon.notify(
                    f"Version {info.latest_version} is available. Open Settings → "
                    "Updates to install it.",
                    "AppTracker — update available",
                )
            else:
                _icon.notify(
                    info.reason or "You are up to date.", "AppTracker — no update"
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Update check failed")
            try:
                _icon.notify(str(exc), "AppTracker — update check failed")
            except Exception:
                pass

    def on_health(_icon, _item) -> None:
        import json
        import urllib.request

        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            llm = data.get("llm") or {}
            email = data.get("email") or {}
            sync = data.get("sync") or {}
            if llm.get("configured"):
                llm_line = f"LLM: {llm.get('name')} ({llm.get('provider')}/{llm.get('model')})"
            else:
                llm_line = "LLM: not configured"
            email_line = (
                f"Email: {email.get('active', 0)}/{email.get('accounts', 0)} active"
            )
            if email.get("last_synced_at"):
                email_line += f" · last sync {email['last_synced_at']}"
            sync_line = (
                f"Sync: {sync.get('message') or sync.get('phase') or 'idle'}"
            )
            msg = f"{llm_line}\n{email_line}\n{sync_line}"
            title = "AppTracker — healthy"
            try:
                _icon.notify(msg, title)
            except Exception:
                logger.info("Health check:\n%s", msg)
        except Exception as exc:
            err = f"Server not responding at {url}\n{exc}"
            try:
                _icon.notify(err, "AppTracker — unhealthy")
            except Exception:
                logger.error(err)

    def startup_label(_item) -> str:
        return "✓ Start on Login" if _is_startup_enabled() else "Start on Login"

    menu = pystray.Menu(
        pystray.MenuItem("Open AppTracker", on_open, default=True),
        pystray.MenuItem("Health Check", on_health),
        pystray.MenuItem("Check for Updates", on_check_updates),
        pystray.MenuItem(startup_label, on_toggle_startup),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    icon = pystray.Icon("AppTracker", icon_image, "AppTracker", menu)

    def quit_for_update() -> None:
        """Shut down on the updater's behalf — only we hold the tray handle."""
        quitting.set()
        server.stop()
        icon.stop()

    def watch_server(tray: "pystray.Icon") -> None:
        """Exit if the server dies, so the instance lock frees up for a relaunch.

        Otherwise a tray icon with no server would hold the lock and every new
        launch would wait on it and then give up.
        """
        tray.visible = True
        server.join()
        if quitting.is_set():
            return
        logger.error("The server stopped unexpectedly; exiting. See %s", LOG_FILE)
        try:
            tray.notify(
                "The server stopped. Start AppTracker again; details are in launcher.log.",
                "AppTracker — stopped",
            )
            time.sleep(5)  # let the notification show before the icon goes
        except Exception:  # noqa: BLE001
            pass
        tray.stop()

    try:
        from app.services import updater as _updater

        _updater.register_quit_hook(quit_for_update)
    except Exception:  # noqa: BLE001 - the tray must still come up
        logger.exception("Could not register the updater quit hook")

    logger.info("Tray ready. Server healthy=%s url=%s", healthy, url)
    icon.run(setup=watch_server)


if __name__ == "__main__":
    main()
