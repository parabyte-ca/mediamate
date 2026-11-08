"""Web Push: notifications on phones and desktops that installed/opened MediaMate.

VAPID keys are generated on first use and kept in the settings table. Every
notification MediaMate sends goes through apprise_notify.notify(), which fans out
to Apprise (if configured) and to every subscribed device here.
"""
import base64
import json
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import PushSubscription, Setting

logger = logging.getLogger(__name__)

PRIVATE_KEY = "vapid_private_key"
PUBLIC_KEY = "vapid_public_key"
# Push services (Google, Mozilla, Apple) want a contact for the sender; it goes to
# them only. Must be a mailto: or a bare https:// host, and Apple rejects localhost.
# Overridable with the push_contact setting.
DEFAULT_SUBJECT = "mailto:mediamate@users.noreply.github.com"
MAX_FAILURES = 5
_key_lock = threading.Lock()


def _session_factory():
    from ..database import SessionLocal
    return SessionLocal()


def get_keys(db: Session) -> tuple[str, str]:
    """(private PEM, public applicationServerKey as base64url) — created on first use."""
    with _key_lock:
        priv = db.get(Setting, PRIVATE_KEY)
        pub = db.get(Setting, PUBLIC_KEY)
        if priv and pub and priv.value and pub.value:
            return priv.value, pub.value
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid02

        v = Vapid02()
        v.generate_keys()
        pem = v.private_pem().decode()
        raw = v.public_key.public_bytes(serialization.Encoding.X962,
                                        serialization.PublicFormat.UncompressedPoint)
        public = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        db.merge(Setting(key=PRIVATE_KEY, value=pem))
        db.merge(Setting(key=PUBLIC_KEY, value=public))
        db.commit()
        return pem, public


def subscribe(db: Session, sub: dict, label: str | None = None, person_id: int | None = None) -> PushSubscription:
    endpoint = sub.get("endpoint") or ""
    keys = sub.get("keys") or {}
    if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("Invalid push subscription")
    row = db.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).first()
    if row is None:
        row = PushSubscription(endpoint=endpoint)
        db.add(row)
    row.p256dh, row.auth, row.label, row.failures = keys["p256dh"], keys["auth"], label, 0
    if person_id is not None:
        row.person_id = person_id
    db.commit()
    return row


def unsubscribe(db: Session, endpoint: str) -> bool:
    n = db.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).delete()
    db.commit()
    return bool(n)


def send_all(title: str, body: str, url: str = "/activity", db: Session | None = None,
             watchlist_id: int | None = None, system: bool = False) -> int:
    """Send to subscribed devices: every one, or (for an alert about a watchlist item, or
    a system alert) the devices of the people it concerns plus devices not linked to
    anyone. Returns how many accepted it. Never raises."""
    own = db is None
    db = db or _session_factory()
    sent = 0
    try:
        subs = db.query(PushSubscription).all()
        if watchlist_id is not None or system:
            from . import people
            who = people.recipients(db, watchlist_id)
            if who is not None:
                subs = [s for s in subs if s.person_id is None or s.person_id in who]
        if not subs:
            return 0
        from py_vapid import Vapid02
        from pywebpush import WebPushException, webpush

        private_pem, _ = get_keys(db)
        contact = db.get(Setting, "push_contact")
        subject = contact.value.strip() if contact and contact.value and contact.value.strip() else DEFAULT_SUBJECT
        if "@" in subject and not subject.startswith("mailto:"):
            subject = f"mailto:{subject}"
        vapid = Vapid02.from_pem(private_pem.encode())  # pywebpush won't take a PEM string; RFC 8292 scheme
        payload = json.dumps({"title": title, "body": body, "url": url})
        for s in subs:
            try:
                webpush(
                    subscription_info={"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}},
                    data=payload, vapid_private_key=vapid,
                    vapid_claims={"sub": subject}, ttl=24 * 3600, timeout=10,
                )
                s.last_success_at = datetime.now(timezone.utc).replace(tzinfo=None)
                s.failures = 0
                sent += 1
            except WebPushException as exc:
                status = getattr(exc.response, "status_code", None)
                if status in (404, 410):  # the browser unsubscribed or the subscription expired
                    db.delete(s)
                else:
                    s.failures = (s.failures or 0) + 1
                    if s.failures >= MAX_FAILURES:
                        db.delete(s)
                    logger.warning("Push to %s… failed: %s", s.endpoint[:40], exc)
            except Exception as exc:
                s.failures = (s.failures or 0) + 1
                logger.warning("Push to %s… failed: %s", s.endpoint[:40], exc)
        db.commit()
    except Exception:
        logger.exception("Web push failed")
    finally:
        if own:
            db.close()
    return sent


def has_subscribers() -> bool:
    db = _session_factory()
    try:
        return db.query(PushSubscription.id).first() is not None
    except Exception:
        return False
    finally:
        db.close()


# Tests set this so pushes go out inline.
SYNC = False


def send_all_async(title: str, body: str, url: str = "/activity", watchlist_id: int | None = None,
                   system: bool = False) -> None:
    kw = {"watchlist_id": watchlist_id, "system": system}
    if SYNC:
        send_all(title, body, url, **kw)
    else:
        threading.Thread(target=send_all, args=(title, body, url), kwargs=kw, name="webpush", daemon=True).start()
