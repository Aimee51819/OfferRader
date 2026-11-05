"""Upgrading a database created by the original application-tracker preserves data."""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

LEGACY_SQL = """
CREATE TABLE applications (id INTEGER PRIMARY KEY, company VARCHAR(255), title VARCHAR(255), location VARCHAR(255),
  url VARCHAR(1024), source VARCHAR(255), salary VARCHAR(255), status VARCHAR(32), description TEXT, skills TEXT,
  notes TEXT, contact_email VARCHAR(255), job_id VARCHAR(128), date_applied DATETIME, created_at DATETIME, updated_at DATETIME);
CREATE TABLE status_events (id INTEGER PRIMARY KEY, application_id INTEGER REFERENCES applications(id), from_status VARCHAR(32),
  to_status VARCHAR(32), note TEXT, source VARCHAR(6), created_at DATETIME);
CREATE TABLE email_accounts (id INTEGER PRIMARY KEY, name VARCHAR(255), imap_host VARCHAR(255), imap_port INTEGER,
  username VARCHAR(255), password_encrypted TEXT, use_ssl BOOLEAN, folder VARCHAR(255), active BOOLEAN,
  last_synced_at DATETIME, last_seen_uid INTEGER, created_at DATETIME);
CREATE TABLE settings (key VARCHAR(255) PRIMARY KEY, value TEXT);
INSERT INTO applications VALUES (1,'IBM','Software Developer Intern',NULL,NULL,'email',NULL,'online_assessment',NULL,NULL,'my note',
  NULL,'128506','2026-10-01 12:00:00','2026-10-01 12:00:00','2026-10-02 12:00:00');
INSERT INTO applications VALUES (2,'Acme','PM Intern',NULL,NULL,'manual',NULL,'saved',NULL,NULL,NULL,NULL,NULL,NULL,
  '2026-10-03 12:00:00','2026-10-03 12:00:00');
INSERT INTO status_events VALUES (1,1,'applied','online_assessment','OA invite','EMAIL','2026-10-02 12:00:00');
INSERT INTO email_accounts VALUES (1,'Gmail','imap.gmail.com',993,'user@example.edu','gAAAA-ciphertext',1,'INBOX',1,NULL,42,
  '2026-10-01 00:00:00');
"""


def _run(data_dir: Path, code: str) -> str:
    env = {"DATA_DIR": str(data_dir), "PATH": "/usr/bin:/bin"}
    return subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True,
                          check=True).stdout


def test_legacy_upgrade_and_downgrade(tmp_path):
    db = tmp_path / "tracker.db"
    con = sqlite3.connect(db)
    con.executescript(LEGACY_SQL)
    con.commit()
    con.close()

    _run(tmp_path, "from app.db import init_db; init_db()")
    con = sqlite3.connect(db)
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    assert {"legacy_applications", "legacy_status_events", "applications", "companies"} <= tables
    rows = con.execute(
        "select c.canonical_name, a.role_title, a.stage, a.external_job_id, a.submission_evidence_type, a.notes "
        "from applications a join companies c on c.id=a.company_id order by c.canonical_name"
    ).fetchall()
    assert rows[0][:2] == ("Acme", "PM Intern") and rows[0][2] == "tracked" and rows[0][4] is None
    ibm = rows[1]
    assert ibm[2] == "assessment_invited" and ibm[3] == "128506" and ibm[4] == "acknowledgment_email"
    assert "my note" in ibm[5] and "legacy id 1" in ibm[5]
    assert con.execute("select count(*) from application_events where event_type='stage_change'").fetchone()[0] == 1
    acc = con.execute("select provider, enabled, credential_ref from email_accounts").fetchone()
    assert acc[0] == "imap" and acc[1] == 0 and acc[2]
    assert con.execute("select count(*) from legacy_applications").fetchone()[0] == 2
    con.close()
    assert list((tmp_path / "backups").glob("pre-migrate-legacy-*.db"))

    _run(tmp_path, (
        "from app.db import _alembic_config; from alembic import command; "
        "command.downgrade(_alembic_config(), 'base')"
    ))
    con = sqlite3.connect(db)
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    assert "legacy_applications" not in tables and "companies" not in tables
    assert con.execute("select count(*) from applications").fetchone()[0] == 2  # original rows intact
    con.close()
