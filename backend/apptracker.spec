# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for a tray-launched AppTracker.exe.

Build from the repo root:
    make dist
  or
    pyinstaller --noconfirm backend/apptracker.spec
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# SPECPATH is the directory holding this file, but PyInstaller has reported it
# differently across versions — locate the backend directory by its contents.
_spec_dir = Path(SPECPATH).resolve()
if (_spec_dir / "launcher.py").exists():
    BACKEND = _spec_dir
elif (_spec_dir / "backend" / "launcher.py").exists():
    BACKEND = _spec_dir / "backend"
else:
    raise SystemExit(f"Cannot locate backend/launcher.py relative to {_spec_dir}")

PROJECT = BACKEND.parent
FRONTEND_DIST = PROJECT / "frontend" / "dist"
ICON = BACKEND / "assets" / "icon.ico"

if not FRONTEND_DIST.is_dir():
    raise SystemExit(
        "frontend/dist is missing — run `npm run build` in frontend/ first, "
        "otherwise the packaged app has no UI to serve."
    )

datas = [(str(FRONTEND_DIST), "frontend/dist")]
if ICON.exists():
    datas.append((str(ICON), "assets"))

# Alembic migrations are loaded from disk at startup.
datas += [(str(BACKEND / "alembic"), "alembic"), (str(BACKEND / "alembic.ini"), ".")]

a = Analysis(
    [str(BACKEND / "launcher.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "app.main",
        "app.api.applications",
        "app.api.backup",
        "app.api.settings",
        "app.api.updates",
        "app.api.dashboard",
        "app.api.reviews",
        "app.api.mail",
        "app.api.io",
        "app.api.integrations",
        "app.services.updater",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AppTracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.exists() else None,
)
