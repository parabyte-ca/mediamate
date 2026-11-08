"""Stored secrets never reach the browser, and the mask round-trips safely."""
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.database import Base, get_db
from app.models import Setting
from app.routers import settings as settings_router
from app.services import secret_settings
from app.services.secret_settings import MASK, SECRET_KEYS, SERVER_ONLY_KEYS

SECRETS = {
    "qbit_password": "qb-pass-7781",
    "jackett_api_key": "jk0123456789abcdef0123456789abcd",
    "apprise_url": "tgram://123456:AAbbCCddEEff/987654",
    "tmdb_api_key": "tmdb0123456789abcdef0123456789ab",
    "trakt_client_id": "trakt0123456789abcdef0123456789abcdef",
    "plex_token": "plexTok-xyz-123",
    "jellyfin_api_key": "jf0123456789abcdef",
    "digest_smtp_password": "smtp-secret-99",
    "ui_password": "hunter2-ui",
    "tpdb_api_key": "tpdb-key-0123456789",
    "session_secret": "a" * 64,
    "vapid_private_key": "-----BEGIN PRIVATE KEY-----\nMIGHAgEA\n-----END PRIVATE KEY-----",
}


@pytest.fixture
def client(tmp_path):
    from app.main import app

    engine = create_engine(f"sqlite:///{tmp_path / 's.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        for k, v in SECRETS.items():
            db.add(Setting(key=k, value=v))
        db.add(Setting(key="plex_url", value="http://plex.local:32400"))
        db.commit()

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    try:
        yield TestClient(app), Session
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def _no_secret_in(body: str):
    for key, value in SECRETS.items():
        assert value not in body, f"{key} leaked"


def test_every_secret_is_covered():
    assert set(SECRETS) == SECRET_KEYS | SERVER_ONLY_KEYS


def test_get_masks_and_hides(client):
    c, _ = client
    r = c.get("/api/settings")
    _no_secret_in(r.text)
    data = r.json()
    assert all(data[k] == MASK for k in SECRET_KEYS)
    assert not SERVER_ONLY_KEYS & data.keys()
    assert data["plex_url"] == "http://plex.local:32400"  # ordinary settings untouched


def test_empty_secret_stays_empty(client):
    c, Session = client
    with Session() as db:
        db.query(Setting).filter(Setting.key == "plex_token").update({"value": ""})
        db.commit()
    assert c.get("/api/settings").json()["plex_token"] == ""


def test_put_with_mask_keeps_secret_and_new_value_replaces(client):
    c, Session = client
    form = c.get("/api/settings").json()
    form["plex_url"] = "http://plex2.local:32400"
    form["jellyfin_api_key"] = "new-jf-key-000"
    r = c.put("/api/settings", json=form)
    _no_secret_in(r.text)
    assert "new-jf-key-000" not in r.text
    with Session() as db:
        stored = {s.key: s.value for s in db.query(Setting)}
    assert stored["plex_token"] == SECRETS["plex_token"]
    assert stored["jellyfin_api_key"] == "new-jf-key-000"
    assert stored["plex_url"] == "http://plex2.local:32400"
    assert stored["vapid_private_key"] == SECRETS["vapid_private_key"]


def test_put_cannot_overwrite_server_only_keys(client):
    c, Session = client
    c.put("/api/settings", json={"session_secret": "x", "vapid_private_key": "y"})
    with Session() as db:
        stored = {s.key: s.value for s in db.query(Setting)}
    assert stored["session_secret"] == SECRETS["session_secret"]
    assert stored["vapid_private_key"] == SECRETS["vapid_private_key"]


def test_test_button_with_mask_uses_stored_key(client, monkeypatch):
    c, _ = client
    seen = {}

    def probe(url, key):
        seen["key"] = key
        return {"ok": False, "error": f"GET {url}?apikey={key} failed"}

    monkeypatch.setattr(settings_router, "probe_jackett", probe)
    r = c.post("/api/settings/test-jackett", json={"url": "http://prowlarr:9696", "api_key": MASK})
    assert seen["key"] == SECRETS["jackett_api_key"]
    _no_secret_in(r.text)  # the error is scrubbed before it is returned


def test_resolve_passes_typed_values_through(client):
    _, Session = client
    with Session() as db:
        assert secret_settings.resolve(db, "plex_token", "typed") == "typed"
        assert secret_settings.resolve(db, "plex_token", MASK) == SECRETS["plex_token"]
        assert secret_settings.resolve(db, "unknown_key", MASK) == ""


def test_url_credentials_are_scrubbed():
    s = secret_settings.scrub_urls(
        "Client error '401' for url 'http://plex:32400/library/sections?X-Plex-Token=abc123&x=1'"
        " and /api/events?token=deadbeef and jackett?apikey=k1&Query=x")
    assert "abc123" not in s and "deadbeef" not in s and "k1&" not in s
    assert "x=1" in s and "Query=x" in s


def test_log_lines_are_redacted(caplog):
    secret_settings.install_log_redaction()
    with caplog.at_level(logging.INFO):
        logging.getLogger("app.test").error("Plex refresh error: %s", "GET http://p/?X-Plex-Token=SeCrEt9")
        logging.getLogger("uvicorn.access").info('%s - "%s %s"', "1.2.3.4", "GET", "/api/events?token=abcdef")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "SeCrEt9" not in text and "abcdef" not in text and MASK in text
