"""/api/doctor — the Library doctor: a weekly check with one-click fixes."""
from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Setting
from ..services import doctor

router = APIRouter(prefix="/doctor", tags=["doctor"])


def _out(db: Session, report: dict | None) -> dict:
    return {"auto_fix": doctor.auto_fix_enabled(db), "report": report}


@router.get("")
def get_report(db: Session = Depends(get_db)):
    return _out(db, doctor.last_report(db))


@router.post("/run")
def run_now(db: Session = Depends(get_db)):
    return _out(db, doctor.run(db))


@router.post("/fix")
def fix(body: dict = Body(...), db: Session = Depends(get_db)):
    ids = [str(i) for i in (body.get("ids") or [])]
    r = doctor.fix_ids(db, ids)
    return {"done": r["done"], "errors": r["errors"], **_out(db, r["report"])}


@router.put("/settings")
def settings(body: dict = Body(...), db: Session = Depends(get_db)):
    if "auto_fix" in body:
        db.merge(Setting(key=doctor.AUTO_KEY, value="true" if body["auto_fix"] else "false"))
        db.commit()
    return _out(db, doctor.last_report(db))
