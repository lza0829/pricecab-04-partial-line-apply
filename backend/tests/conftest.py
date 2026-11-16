import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# Isolate the test database before any app module reads DATA_DIR.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="pricecab-test-")

import sqlite3  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture()
def db_path():
    """Freshly seeded database per test."""
    from app.config import DB_PATH
    from app.seed import seed_if_empty

    if DB_PATH.exists():
        DB_PATH.unlink()
    seed_if_empty()
    return DB_PATH


@pytest.fixture()
def client(db_path):
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def sql(db_path, statement, params=()):
    """Run a statement directly against the test database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(statement, params)
        rows = cur.fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()
