"""Errors a route doesn't handle come back as a readable message (secrets masked),
and "database is locked" says MediaMate is busy instead of a bare HTTP 500."""
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.database import BUSY_TIMEOUT_MS
from app.main import app


@pytest.fixture
def boom():
    calls = {}

    @app.get("/api/__test_boom")
    def _boom(kind: str):
        if kind == "locked":
            raise OperationalError("UPDATE music_albums ...", {}, sqlite3.OperationalError("database is locked"))
        raise RuntimeError("Couldn't reach http://indexer/api?apikey=abcdef1234567890 (timed out)")
    yield calls
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/api/__test_boom"]


def test_database_locked_says_busy(boom):
    r = TestClient(app, raise_server_exceptions=False).get("/api/__test_boom?kind=locked")
    assert r.status_code == 503 and "busy saving something else" in r.json()["detail"]


def test_other_errors_explain_themselves_without_secrets(boom):
    r = TestClient(app, raise_server_exceptions=False).get("/api/__test_boom?kind=other")
    assert r.status_code == 500
    d = r.json()["detail"]
    assert d.startswith("Something went wrong (RuntimeError: Couldn't reach") and "abcdef1234567890" not in d


def test_saves_wait_for_each_other():
    assert BUSY_TIMEOUT_MS >= 30000
