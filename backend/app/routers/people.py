"""/api/people — who's in the household (each with their own watchlist view)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Person
from ..services import people

router = APIRouter()


@router.get("/people")
def list_people(db: Session = Depends(get_db)):
    people.ensure_owner(db)
    return [people.out(p) for p in db.query(Person).order_by(Person.id)]


def _name(body: dict) -> str:
    name = str(body.get("name") or "").strip()[:40]
    if not name:
        raise HTTPException(status_code=400, detail="Give them a name")
    return name


@router.post("/people", status_code=201)
def add_person(body: dict = Body(...), db: Session = Depends(get_db)):
    people.ensure_owner(db)
    name = _name(body)
    if db.query(Person).filter(Person.name.ilike(name)).first():
        raise HTTPException(status_code=409, detail=f"{name} is already here")
    n = db.query(Person).count()
    p = Person(name=name, color=body.get("color") or people.COLORS[n % len(people.COLORS)])
    db.add(p)
    db.commit()
    return people.out(p)


@router.patch("/people/{person_id}")
def update_person(person_id: int, body: dict = Body(...), db: Session = Depends(get_db)):
    p = db.get(Person, person_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Not found")
    if "name" in body:
        p.name = _name(body)
    if body.get("color"):
        p.color = str(body["color"])[:16]
    db.commit()
    return people.out(p)


@router.delete("/people/{person_id}")
def remove_person(person_id: int, db: Session = Depends(get_db)):
    p = db.get(Person, person_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        people.remove(db, p)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/people/{person_id}/avatar")
async def set_avatar(person_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    p = db.get(Person, person_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        p.avatar = people.make_avatar(await file.read(people.MAX_AVATAR + 1))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    p.avatar_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return people.out(p)


@router.delete("/people/{person_id}/avatar")
def remove_avatar(person_id: int, db: Session = Depends(get_db)):
    p = db.get(Person, person_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Not found")
    p.avatar = p.avatar_at = None
    db.commit()
    return people.out(p)


@router.get("/people/{person_id}/avatar")
def get_avatar(person_id: int, db: Session = Depends(get_db)):
    p = db.get(Person, person_id)
    if p is None or not p.avatar:
        raise HTTPException(status_code=404, detail="No picture")
    return Response(p.avatar, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000"})
