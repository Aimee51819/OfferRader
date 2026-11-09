"""Engine, sessions, and schema migration (Alembic) for the local SQLite store."""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

logger = logging.getLogger("tracker.db")

engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False, "timeout": 30},
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    """WAL so background sync never blocks the UI; enforce foreign keys."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

# Sync/processing sessions read, wait on the local model for seconds, then write. With
# deferred transactions another connection's commit (e.g. the scheduler heartbeat) in
# that gap makes the later write fail immediately with "database is locked" (stale WAL
# snapshot; busy_timeout does not apply). Taking the write lock up front with
# BEGIN IMMEDIATE makes such writers queue instead. Only the sync pipeline uses this
# engine, so UI requests keep ordinary (non-blocking) read transactions.
sync_engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False, "timeout": 30},
    future=True,
)
event.listen(sync_engine, "connect", _set_sqlite_pragma)


@event.listens_for(sync_engine, "connect")
def _manual_transactions(dbapi_connection, _connection_record) -> None:
    dbapi_connection.isolation_level = None  # we emit BEGIN ourselves


@event.listens_for(sync_engine, "begin")
def _begin_immediate(conn) -> None:
    conn.exec_driver_sql("BEGIN IMMEDIATE")


SyncSessionLocal = sessionmaker(bind=sync_engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_BACKEND_DIR = Path(__file__).resolve().parent.parent


def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.db_url)
    return cfg


def _current_and_head() -> tuple[str | None, str | None]:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    return current, head


def backup_database(label: str) -> Path | None:
    """Consistent SQLite copy into DATA_DIR/backups (works with WAL)."""
    db_file = settings.data_path / "tracker.db"
    if not db_file.exists() or db_file.stat().st_size == 0:
        return None
    target_dir = settings.data_path / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"{label}-{stamp}.db"
    src = sqlite3.connect(str(db_file))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return target


def init_db() -> None:
    """Bring the database to the latest schema. Backs up first if anything will change."""
    from alembic import command

    from . import models  # noqa: F401 - register metadata

    current, head = _current_and_head()
    if current != head:
        has_tables = bool(inspect(engine).get_table_names())
        if has_tables:
            path = backup_database(f"pre-migrate-{current or 'legacy'}")
            if path:
                logger.info("Backed up database to %s before migrating", path)
        command.upgrade(_alembic_config(), "head")
    _seed_defaults()


def _seed_defaults() -> None:
    from .services.settings_service import seed_defaults

    db = SessionLocal()
    try:
        seed_defaults(db)
    finally:
        db.close()
