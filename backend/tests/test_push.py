"""Web Push: VAPID keys, subscriptions, delivery and clean-up, and the notify fan-out."""
import base64
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.database import Base, get_db
from app.models import PushSubscription
from app.services import apprise_notify, webpush


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def browser_subscription(endpoint="https://push.example.com/send/abc"):
    """Keys shaped exactly like a real browser's PushSubscription."""
    key = ec.generate_private_key(ec.SECP256R1())
    p256dh = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"endpoint": endpoint, "keys": {"p256dh": b64(p256dh), "auth": b64(b"0123456789abcdef")}}


@pytest.fixture
def Session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'push.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    monkeypatch.setattr(webpush, "_session_factory", S)
    monkeypatch.setattr(webpush, "SYNC", True)
    yield S
    engine.dispose()


def test_keys_are_created_once_and_valid(Session):
    with Session() as db:
        pem, public = webpush.get_keys(db)
        assert webpush.get_keys(db) == (pem, public)
    raw = base64.urlsafe_b64decode(public + "=" * (-len(public) % 4))
    assert len(raw) == 65 and raw[0] == 4  # uncompressed P-256 point, what browsers expect
    assert "PRIVATE KEY" in pem


def test_subscribe_validates_and_upserts(Session):
    sub = browser_subscription()
    with Session() as db:
        with pytest.raises(ValueError):
            webpush.subscribe(db, {"endpoint": "http://insecure", "keys": sub["keys"]})
        with pytest.raises(ValueError):
            webpush.subscribe(db, {"endpoint": sub["endpoint"], "keys": {}})
        a = webpush.subscribe(db, sub, "Phone")
        b = webpush.subscribe(db, sub, "Phone (renamed)")
        assert a.id == b.id and db.query(PushSubscription).count() == 1
        assert webpush.unsubscribe(db, sub["endpoint"]) and not webpush.unsubscribe(db, sub["endpoint"])


def test_real_encryption_and_vapid_signing(Session):
    """pywebpush encrypts with our keys for a real-shaped subscription; only the HTTP call is faked."""
    with Session() as db:
        webpush.subscribe(db, browser_subscription(), "Laptop")
    ok = MagicMock(status_code=201, text="")
    with patch("requests.post", return_value=ok) as post:
        assert webpush.send_all("Title", "Body") == 1
    headers = post.call_args.kwargs["headers"]
    assert headers["content-encoding"] == "aes128gcm"
    assert headers["authorization"].startswith("vapid t=")
    assert len(post.call_args.kwargs["data"]) > 100  # encrypted payload, not plaintext
    with Session() as db:
        assert db.query(PushSubscription).one().last_success_at is not None


def test_gone_subscriptions_are_removed_and_flaky_ones_after_repeated_failures(Session):
    from pywebpush import WebPushException

    with Session() as db:
        webpush.subscribe(db, browser_subscription("https://push.example.com/gone"))
        webpush.subscribe(db, browser_subscription("https://push.example.com/flaky"))

    def fake(subscription_info, **_):
        status = 410 if subscription_info["endpoint"].endswith("gone") else 500
        raise WebPushException("nope", response=MagicMock(status_code=status))

    with patch("pywebpush.webpush", side_effect=fake):
        assert webpush.send_all("t", "b") == 0
        with Session() as db:
            assert [s.endpoint for s in db.query(PushSubscription)] == ["https://push.example.com/flaky"]
        for _ in range(webpush.MAX_FAILURES):
            webpush.send_all("t", "b")
    with Session() as db:
        assert db.query(PushSubscription).count() == 0


def test_no_subscribers_is_a_noop(Session):
    with patch("pywebpush.webpush") as wp:
        assert webpush.send_all("t", "b") == 0
        wp.assert_not_called()


def test_notify_fans_out_to_push_and_apprise(Session):
    with patch.object(webpush, "send_all") as push, patch("apprise.Apprise") as ap:
        apprise_notify.notify("", "Title", "Body")
        push.assert_called_once_with("Title", "Body", "/activity", watchlist_id=None, system=True)
        ap.assert_not_called()  # no Apprise URL: push only
        apprise_notify.notify("json://x", "T", "B", url="/library")
        assert push.call_args.args == ("T", "B", "/library")
        ap.return_value.notify.assert_called_once_with(title="T", body="B")


def test_api(Session):
    from app.main import app

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    try:
        client = TestClient(app)
        cfg = client.get("/api/push/config").json()
        assert cfg["public_key"] and cfg["devices"] == []
        assert client.post("/api/push/subscribe", json={"subscription": {"endpoint": "x"}}).status_code == 400
        sub = browser_subscription()
        assert client.post("/api/push/subscribe", json={"subscription": sub, "label": "Pixel"}).json()["ok"]
        devices = client.get("/api/push/config").json()["devices"]
        assert [d["label"] for d in devices] == ["Pixel"]
        with patch("pywebpush.webpush"):
            assert client.post("/api/push/test").json() == {"ok": True, "sent": 1}
        assert client.delete(f"/api/push/devices/{devices[0]['id']}").json() == {"ok": True}
        assert client.post("/api/push/test").status_code == 400
        assert client.post("/api/push/unsubscribe", json={"endpoint": sub["endpoint"]}).json() == {"ok": False}
    finally:
        app.dependency_overrides.pop(get_db, None)
