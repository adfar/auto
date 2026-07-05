"""Make top-level modules (db, forecaster, ...) importable and point the
shared SQLite DB at a per-test temp file so tests never touch kalshi_edge.db."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    return db.connect()
