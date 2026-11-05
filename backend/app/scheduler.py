"""Background jobs: mailbox sync (opt-in), reminders, and a heartbeat.

APScheduler is configured with coalesce=True and no misfire grace limit, so
after the Mac wakes from sleep each job runs once (not once per missed tick)
and sync resumes from the last committed cursor.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from .db import SessionLocal
from .services import settings_service

logger = logging.getLogger("tracker.scheduler")

scheduler: BackgroundScheduler | None = None
SYNC_JOB = "email_sync"
NOTIFY_JOB = "reminders"
HEARTBEAT_JOB = "heartbeat"


def _sync_job() -> None:
    db = SessionLocal()
    try:
        enabled = bool(settings_service.get(db, "auto_sync_enabled"))
    finally:
        db.close()
    if not enabled:
        return
    from .services.sync import sync_all

    sync_all()


def _notify_job() -> None:
    from .services.notifications import evaluate

    db = SessionLocal()
    try:
        evaluate(db)
    finally:
        db.close()


def _heartbeat() -> None:
    db = SessionLocal()
    try:
        settings_service.set_value(db, "scheduler_heartbeat_at", datetime.now(timezone.utc).isoformat())
        db.commit()
    finally:
        db.close()


def start(interval_seconds: int) -> None:
    global scheduler
    if scheduler is not None and scheduler.running:
        return
    scheduler = BackgroundScheduler(
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": None}
    )
    now = datetime.now(timezone.utc)
    # First sync shortly after start (catch-up after a restart); a no-op unless enabled.
    scheduler.add_job(_sync_job, "interval", seconds=interval_seconds, id=SYNC_JOB,
                      next_run_time=now + timedelta(seconds=30))
    scheduler.add_job(_notify_job, "interval", minutes=5, id=NOTIFY_JOB, next_run_time=now + timedelta(seconds=10))
    scheduler.add_job(_heartbeat, "interval", minutes=1, id=HEARTBEAT_JOB, next_run_time=now)
    scheduler.start()
    logger.info("Scheduler started (sync every %ss when enabled)", interval_seconds)


def reschedule(interval_seconds: int) -> None:
    if scheduler is not None and scheduler.running:
        scheduler.reschedule_job(SYNC_JOB, trigger="interval", seconds=interval_seconds)


def shutdown() -> None:
    global scheduler
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=True)
    scheduler = None


def status() -> dict:
    if scheduler is None or not scheduler.running:
        return {"running": False}
    job = scheduler.get_job(SYNC_JOB)
    return {
        "running": True,
        "next_sync_at": job.next_run_time.isoformat() if job and job.next_run_time else None,
    }
