import os
import shutil
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DownloadPath, Setting
from ..schemas import DownloadPathCreate, DownloadPathOut, DownloadPathUpdate
from ..services import folders
from ..services.disk_space import is_low, threshold_bytes

router = APIRouter()


def _threshold(db: Session) -> int:
    row = db.query(Setting).filter(Setting.key == "low_disk_warn_gb").first()
    return threshold_bytes({"low_disk_warn_gb": row.value if row else "10"})


def _with_disk_usage(db: Session, dp: DownloadPath, threshold: int = 0) -> dict:
    """Return a dict suitable for DownloadPathOut, with disk-usage fields populated."""
    local = folders.local_of(db, dp)
    out = {
        "id": dp.id,
        "name": dp.name,
        "path": dp.path,
        "is_default": dp.is_default,
        "local_path": dp.local_path,
        "local": local,
        "exists_here": bool(local) and os.path.isdir(local),
        "downloads": bool(dp.downloads),
        "scan": bool(dp.scan),
        "free_bytes": None,
        "total_bytes": None,
        "low_space": False,
        "target": dp.target or "auto",
        "target_min": dp.target_min,
        "target_max": dp.target_max,
        "target_shrink": bool(dp.target_shrink),
        "target_summary": _target_summary(dp),
        "protected": bool(dp.protected),
    }
    try:
        usage = shutil.disk_usage(local)  # measured here, where ScreenMate sees the folder
        out["free_bytes"] = usage.free
        out["total_bytes"] = usage.total
        out["low_space"] = is_low(usage.free, threshold)
    except Exception:
        pass
    return out


@router.get("/paths", response_model=list[DownloadPathOut])
def get_paths(db: Session = Depends(get_db)):
    threshold = _threshold(db)
    return [_with_disk_usage(db, dp, threshold) for dp in db.query(DownloadPath).all()]


def _target_summary(dp: DownloadPath) -> str:
    from ..services.media import targets
    kind = dp.target or "auto"
    if kind == "music":
        return "Music: FLAC kept as it is; never encoded"
    if kind == "auto":
        return "H.265; TV 720p–1080p, movies 1080p–4K (by file name)"
    t = targets.folder_target(kind, dp.target_min, dp.target_max, dp.target_shrink, "", dp.name)
    span = targets.LABEL[t.min] if t.min == t.max else f"{targets.LABEL[t.min]}–{targets.LABEL[t.max]}"
    return f"H.265, {span}" + (", shrink bigger files when encoding" if t.shrink else "")


def _guess_target(name: str) -> dict:
    n = (name or "").lower()
    if "bellesa" in n:
        return {"target": "custom", "target_min": 720, "target_max": 720, "target_shrink": True}
    if "movie" in n or "film" in n:
        return {"target": "movies"}
    if "tv" in n or "show" in n or "series" in n:
        return {"target": "tv"}
    return {"target": "auto"}


def _clean_target(values: dict) -> dict:
    from ..services.media import targets
    for key in ("target_min", "target_max"):
        if key in values and values[key] is not None and values[key] not in targets.HEIGHTS:
            raise HTTPException(status_code=400, detail="Pick a resolution from the list")
    for key in ("target", "target_shrink", "target_min", "target_max", "protected"):
        if key in values and values[key] is None:
            values.pop(key)
    return values


def _reclassify(db: Session, dp: DownloadPath) -> None:
    """Targets changed: re-check this folder's pending / in-target files."""
    try:
        from ..services.media import targets
        targets.reclassify(folders.local_of(db, dp))
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Re-checking target formats failed")


def _clean(values: dict, current: DownloadPath | None = None) -> dict:
    """Keep a row consistent: only a download folder can be the default, and a
    library-only folder is the same path for qBittorrent and here."""
    downloads = values.get("downloads", current.downloads if current else True)
    for key in ("path", "local_path"):
        if isinstance(values.get(key), str):
            values[key] = values[key].strip() or None
    if values.get("path") is None and "path" in values:
        raise HTTPException(status_code=400, detail="Enter the folder's path")
    if not downloads:
        if values.get("is_default"):
            raise HTTPException(status_code=400, detail="Only a download folder can be the default")
        values["is_default"] = False
        if not values.get("local_path"):
            values["local_path"] = (current.local_path if current else None) or values.get("path") \
                or (current.path if current else None)
    return values


@router.get("/paths/suggestions")
def path_suggestions(db: Session = Depends(get_db)):
    """Folders qBittorrent uses that aren't in the list yet (read-only)."""
    return folders.suggestions(db)


@router.post("/paths", response_model=DownloadPathOut, status_code=201)
def create_path(path: DownloadPathCreate, db: Session = Depends(get_db)):
    values = _clean_target(_clean(path.model_dump()))
    if "target" not in values:
        values.update(_guess_target(values.get("name", "")))
    if values.get("protected") is None:
        from ..services import adult
        values["protected"] = adult.guess_protected(values.get("name", "")) or \
            adult.guess_protected(values.get("path", ""))
    if values.get("is_default"):
        db.query(DownloadPath).update({"is_default": False})
    db_path = DownloadPath(**values)
    db.add(db_path)
    db.commit()
    db.refresh(db_path)
    folders.refresh(db)
    _reclassify(db, db_path)
    return _with_disk_usage(db, db_path, _threshold(db))


@router.put("/paths/{path_id}", response_model=DownloadPathOut)
def update_path(path_id: int, path: DownloadPathUpdate, db: Session = Depends(get_db)):
    db_path = db.query(DownloadPath).filter(DownloadPath.id == path_id).first()
    if not db_path:
        raise HTTPException(status_code=404, detail="Path not found")
    updates = _clean_target(_clean(path.model_dump(exclude_unset=True), db_path))
    if updates.get("is_default"):
        db.query(DownloadPath).update({"is_default": False})
    target_changed = any(k.startswith("target") for k in updates) or "local_path" in updates or "path" in updates
    for field, value in updates.items():
        setattr(db_path, field, value)
    db.commit()
    db.refresh(db_path)
    folders.refresh(db)
    if target_changed:
        _reclassify(db, db_path)
    return _with_disk_usage(db, db_path, _threshold(db))


@router.delete("/paths/{path_id}")
def delete_path(path_id: int, db: Session = Depends(get_db)):
    db_path = db.query(DownloadPath).filter(DownloadPath.id == path_id).first()
    if not db_path:
        raise HTTPException(status_code=404, detail="Path not found")
    folders.forget_env(db, folders.local_of(db, db_path))
    db.delete(db_path)
    db.commit()
    folders.refresh(db)
    return {"ok": True}


@router.post("/paths/{path_id}/test")
def test_path(path_id: int, db: Session = Depends(get_db)):
    """Check a saved folder: visible, readable, writable, free space, and qBittorrent's view."""
    dp = db.query(DownloadPath).filter(DownloadPath.id == path_id).first()
    if dp is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    return folders.check(db, dp, _threshold(db))
