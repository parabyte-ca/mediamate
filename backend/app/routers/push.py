"""/api/push — Web Push subscriptions for this browser/phone."""
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import PushSubscription
from ..services import webpush

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/config")
def config(db: Session = Depends(get_db)):
    _, public = webpush.get_keys(db)
    devices = [{"id": s.id, "label": s.label, "person_id": s.person_id,
                "created_at": s.created_at.isoformat() if s.created_at else None}
               for s in db.query(PushSubscription).order_by(PushSubscription.id)]
    return {"public_key": public, "devices": devices}


@router.post("/subscribe")
def subscribe(body: dict = Body(...), db: Session = Depends(get_db)):
    try:
        pid = body.get("person_id")
        row = webpush.subscribe(db, body.get("subscription") or {}, (body.get("label") or "")[:80] or None,
                                int(pid) if str(pid or "").isdigit() else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id": row.id}


@router.post("/person")
def set_person(body: dict = Body(...), db: Session = Depends(get_db)):
    """This device now belongs to someone else (the person picker changed)."""
    row = db.query(PushSubscription).filter(PushSubscription.endpoint == (body.get("endpoint") or "")).first()
    if row is None:
        return {"ok": False}
    pid = body.get("person_id")
    row.person_id = int(pid) if str(pid or "").isdigit() else None
    db.commit()
    return {"ok": True}


@router.post("/unsubscribe")
def unsubscribe(body: dict = Body(...), db: Session = Depends(get_db)):
    return {"ok": webpush.unsubscribe(db, body.get("endpoint") or "")}


@router.delete("/devices/{device_id}")
def remove_device(device_id: int, db: Session = Depends(get_db)):
    row = db.get(PushSubscription, device_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Device not found")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/test")
def test(db: Session = Depends(get_db)):
    sent = webpush.send_all("MediaMate", "Push notifications are working 🎉", "/activity", db=db)
    if not sent:
        raise HTTPException(status_code=400, detail="No device accepted the notification — enable it on this device first")
    return {"ok": True, "sent": sent}
