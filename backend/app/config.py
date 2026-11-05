from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "AppTracker"

# Repo-relative locations used before the app stored data per-user. A packaged
# build must not write next to the executable, and a cloud-synced checkout
# corrupts SQLite WAL files, so these are migrated once and left in place.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_LEGACY_DATA_DIRS = (
    _PROJECT_ROOT / "data",
    _PROJECT_ROOT / "backend" / "data",
)
_MIGRATED_FILES = (
    "tracker.db",
    "tracker.db-wal",
    "tracker.db-shm",
    "secret.key",
)

_migration_checked = False


def default_data_dir() -> Path:
    """Per-user writable location for the database and encryption key."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / APP_NAME


def _migrate_legacy_data(target: Path) -> None:
    """Copy a repo-local database into the per-user directory, once.

    Copies rather than moves so an older build pointed at the old path still
    starts, and never overwrites a database that already exists at the target.
    """
    global _migration_checked
    if _migration_checked:
        return
    _migration_checked = True
    if (target / "tracker.db").exists():
        return
    for legacy in _LEGACY_DATA_DIRS:
        try:
            if legacy.resolve() == target.resolve():
                continue
        except OSError:
            continue
        if not (legacy / "tracker.db").exists():
            continue
        target.mkdir(parents=True, exist_ok=True)
        for name in _MIGRATED_FILES:
            source = legacy / name
            if source.exists():
                shutil.copy2(source, target / name)
        (target / "MIGRATED_FROM.txt").write_text(
            f"Copied from {legacy}\n", encoding="utf-8"
        )
        return


class Settings(BaseSettings):
    """Application configuration loaded from environment / .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Unset means "use the per-user app data directory".
    data_dir: str | None = None
    frontend_origin: str = "http://localhost:5173"
    email_poll_interval_seconds: int = 900
    app_secret_key: str | None = None
    # Keep the Fernet key in the macOS Keychain (needs `keyring`) instead of DATA_DIR/secret.key.
    # Off by default so Settings -> Backup can carry the key with the database.
    use_keychain: bool = False

    # --- Local model (Ollama only; there is no remote inference fallback) ---
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b-instruct-2507-q4_K_M"
    ollama_timeout_seconds: int = 180
    ollama_max_retries: int = 2
    # Cap what we send to the model (characters of cleaned body text).
    max_model_input_chars: int = 6000

    # --- Server binding / local security ---
    # Loopback only by default. Anything else requires ALLOW_NON_LOOPBACK=true.
    bind_host: str = "127.0.0.1"
    allow_non_loopback: bool = False
    # Shared secret for POST /api/integrations/*. Generated on first run if unset.
    integration_token: str | None = None

    # --- Display ---
    display_timezone: str = "America/New_York"

    # --- OAuth app registrations (public "desktop" clients, PKCE) ---
    google_client_id: str | None = None
    # Google issues a client "secret" for desktop apps that is not confidential;
    # it is still kept out of git (.env only).
    google_client_secret: str | None = None
    microsoft_client_id: str | None = None
    # "common" (work+personal), "consumers" (outlook.com), or a tenant id.
    microsoft_tenant: str = "common"
    # Public URL of *this* backend used in OAuth redirect URIs (loopback).
    oauth_redirect_base: str = "http://127.0.0.1:8000"

    # --- Mail handling limits ---
    max_body_bytes: int = 512_000
    max_messages_per_sync: int = 200

    @property
    def data_path(self) -> Path:
        if self.data_dir:
            path = Path(self.data_dir).resolve()
            path.mkdir(parents=True, exist_ok=True)
            return path
        path = default_data_dir()
        _migrate_legacy_data(path)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_path / 'tracker.db'}"

    @property
    def secret_key_file(self) -> Path:
        return self.data_path / "secret.key"


settings = Settings()
