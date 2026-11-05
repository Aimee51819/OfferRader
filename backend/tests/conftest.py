"""Pytest fixtures. DATA_DIR is pointed at a temp dir before the app imports."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# Must be set before any `app.*` import so the engine binds to a throwaway DB.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="tracker-test-")
os.environ.setdefault("INTEGRATION_TOKEN", "test-integration-token")

import pytest  # noqa: E402

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "emails"


def load_fixtures() -> dict[str, dict]:
    return {
        f.stem: json.loads(f.read_text(encoding="utf-8")) for f in sorted(FIXTURE_DIR.glob("*.json"))
    }


@pytest.fixture(scope="session")
def _schema():
    from app.db import init_db

    init_db()
    return True


@pytest.fixture()
def db(_schema):
    from app.db import Base, SessionLocal, engine
    from app.services.settings_service import seed_defaults

    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    session = SessionLocal()
    seed_defaults(session)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="session")
def fixtures() -> dict[str, dict]:
    return load_fixtures()


@pytest.fixture()
def replay(fixtures):
    from app.services.llm.offline import ReplayBackend

    return ReplayBackend({f["subject"]: f["model_output"] for f in fixtures.values()})


@pytest.fixture(scope="session")
def app_client(_schema):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture()
def client(app_client, db):
    return app_client
