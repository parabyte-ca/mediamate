"""Security audit 2: no JSON response carries a secret.

1. The response filter masks stored secret values and URL credentials in any JSON
   (an httpx error naming its URL can't leak a key).
2. Every GET route, called with secrets seeded and the filter's value list empty,
   returns none of them by itself; the filter is the backstop, not the fix."""
import time

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.testclient import TestClient

from app.middleware import scrub
from app.models import Setting

from tests.test_organise import lib  # noqa: F401
from tests.test_pipeline import env  # noqa: F401

SECRETS = {
    "plex_token": "PLEXTOKEN-abcdef123456", "tmdb_api_key": "TMDBKEY-0123456789abcdef",
    "jackett_api_key": "JACKETTKEY-zyxwvu987654", "qbit_password": "QBITPASS-hunter2hunter2",
    "jellyfin_api_key": "JELLYKEY-5555666677778888", "apprise_url": "tgram://BOTTOKEN-112233445566/chat",
    "digest_smtp_password": "SMTPPASS-mailmailmail", "ui_password": "UIPASS-letmeinletmein",
    "session_secret": "SESSIONSECRET-0000111122223333", "vapid_private_key": "VAPIDPRIVATE-aaaabbbbccccdddd",
    "tpdb_api_key": "TPDBKEY-9999888877776666", "trakt_client_id": "TRAKTID-1234abcd5678efgh",
}


@pytest.fixture
def filtered(monkeypatch):
    monkeypatch.setitem(scrub._cache, "at", time.monotonic())
    monkeypatch.setitem(scrub._cache, "values", ("STORED-SECRET-VALUE-42",))
    app = FastAPI()
    app.add_middleware(scrub.ScrubMiddleware)

    @app.get("/api/boom")
    def boom():
        raise HTTPException(502, "Could not reach TMDB (https://api.themoviedb.org/3/x?api_key=abc123def456)")

    @app.get("/api/echo")
    def echo():
        return {"msg": "Plex said no: STORED-SECRET-VALUE-42", "token_field": "fine"}

    @app.get("/api/plain")
    def plain():
        return PlainTextResponse("STORED-SECRET-VALUE-42")

    @app.get("/api/stream")
    def stream():
        return StreamingResponse(iter([b"data: STORED-SECRET-VALUE-42\n\n"]), media_type="text/event-stream")
    return TestClient(app)


def test_errors_and_values_are_masked(filtered):
    r = filtered.get("/api/boom")
    assert r.status_code == 502 and "abc123def456" not in r.text and "api_key=" + scrub.MASK in r.json()["detail"]
    r = filtered.get("/api/echo").json()
    assert r == {"msg": f"Plex said no: {scrub.MASK}", "token_field": "fine"}


def test_files_and_streams_pass_through(filtered):
    assert filtered.get("/api/plain").text == "STORED-SECRET-VALUE-42"
    assert "STORED-SECRET-VALUE-42" in filtered.get("/api/stream").text


def test_no_get_route_returns_a_secret(lib, monkeypatch):
    client, Session, media = lib
    with Session() as s:
        for k, v in SECRETS.items():
            s.merge(Setting(key=k, value=v))
        s.merge(Setting(key="plex_enabled", value="true"))
        s.merge(Setting(key="plex_url", value="http://plex.invalid:32400"))
        s.commit()
    monkeypatch.setitem(scrub._cache, "at", time.monotonic() + 3600)   # the filter knows no values:
    monkeypatch.setitem(scrub._cache, "values", ())                    # routes must mask by themselves
    from app.main import app
    skip = {"/api/events", "/api/backup/export"}                        # a stream; POST only
    leaks, checked = [], 0
    for route in app.routes:
        path = getattr(route, "path", "")
        if "GET" not in (getattr(route, "methods", None) or ()) or not path.startswith("/api/") \
                or "{" in path or path in skip:
            continue
        r = client.get(path)
        checked += 1
        if "json" not in r.headers.get("content-type", ""):
            continue
        for k, v in SECRETS.items():
            if v in r.text:
                leaks.append((path, k))
    assert checked > 30 and not leaks, leaks
