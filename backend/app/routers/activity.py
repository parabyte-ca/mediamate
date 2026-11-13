"""Activity — journeys for the Activity page (replaces the Downloads list)."""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Download, Journey, MediaJob, PipelinePlan, Setting
from ..services import journeys

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/activity", tags=["activity"])

_FILTERS = {"all", "active", "failed", "done_today"}


def _get(db: Session, journey_id: int) -> Journey:
    j = db.query(Journey).filter(Journey.id == journey_id).first()
    if j is None:
        raise HTTPException(status_code=404, detail="Not found")
    return j


@router.get("")
def list_activity(
    request: Request,
    filter: str = Query("all"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    who: str = Query("mine", description="mine (downloads of the shows this device's person follows) or all"),
    db: Session = Depends(get_db),
):
    if filter not in _FILTERS:
        raise HTTPException(status_code=400, detail=f"filter must be one of {sorted(_FILTERS)}")
    q = db.query(Journey)
    from ..services import people
    me = people.current(db, request)
    if who != "all" and me is not None:
        people.ensure_owner(db)
        mine = people.followed_by(db, me.id)
        q = q.join(Download, Download.id == Journey.download_id).filter(Download.watchlist_id.in_(mine))
    if filter == "active":
        q = q.filter(Journey.status == "active")
    elif filter == "failed":
        q = q.filter(Journey.status == "failed")
    elif filter == "done_today":
        midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        since = midnight.astimezone(timezone.utc).replace(tzinfo=None)
        q = q.filter(Journey.status == "done", Journey.updated_at >= since)
    total = q.count()
    rows = q.order_by(Journey.updated_at.desc(), Journey.id.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": [journeys.snapshot(db, j) for j in rows]}


@router.get("/stats")
def activity_stats(db: Session = Depends(get_db)):
    return journeys.stats(db)


@router.get("/{journey_id}")
def get_activity(journey_id: int, db: Session = Depends(get_db)):
    return journeys.snapshot(db, _get(db, journey_id), with_logs=True)


def _current(db: Session, j: Journey) -> dict:
    snap = journeys.snapshot(db, j)
    if snap["current_step"] < 0:
        raise HTTPException(status_code=409, detail="Nothing to act on")
    return snap["steps"][snap["current_step"]]


@router.post("/{journey_id}/cancel")
def cancel(journey_id: int, db: Session = Depends(get_db)):
    j = _get(db, journey_id)
    step = _current(db, j)
    if step["status"] not in ("pending", "running"):
        raise HTTPException(status_code=409, detail=f"{step['label']} is {step['status']}, nothing to cancel")
    ref = step["ref"]
    if ref["type"] == "plan":
        from ..services import pipeline

        pipeline.cancel_plan(db.get_bind(), ref["id"])
        return {"ok": True, "msg": "Remaining steps cancelled"}
    if ref["type"] == "media_job":
        from .media import _cancel_job_by_id

        result = _cancel_job_by_id(ref["id"])
        if not isinstance(result, dict):  # an error JSONResponse from the media router
            raise HTTPException(status_code=result.status_code, detail="Could not cancel job")
        return result

    dl = db.query(Download).filter(Download.id == ref["id"]).first()
    if dl is None:
        raise HTTPException(status_code=404, detail="Download not found")
    if dl.torrent_hash and not dl.qbit_removed:
        from ..services.qbittorrent import remove_torrent

        try:
            # A cancelled download's partial data is useless — remove it too.
            remove_torrent(dl.torrent_hash, delete_files=True)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"qBittorrent: {exc}")
        dl.qbit_removed = True
    dl.status = "cancelled"
    db.commit()
    return {"ok": True, "msg": "Download cancelled"}


@router.post("/{journey_id}/cancel-all")
def cancel_all(journey_id: int, db: Session = Depends(get_db)):
    """Stop everything still to do in this journey: running and waiting jobs, and the
    remaining steps of its files' plans (a season pack's other episodes)."""
    from ..models import PipelinePlan
    from ..services import pipeline
    from .media import _cancel_job_by_id
    j = _get(db, journey_id)
    jobs = db.query(MediaJob).filter(MediaJob.journey_id == j.id, MediaJob.status.in_(["queued", "running"])).all()
    running = sum(1 for x in jobs if x.status == "running")
    waiting = len(jobs) - running
    plans = [p.id for p in db.query(PipelinePlan).filter(PipelinePlan.journey_id == j.id,
                                                         PipelinePlan.status.in_(["waiting", "running"]))]
    for plan_id in plans:                      # first, so finished jobs don't queue the next step
        pipeline.cancel_plan(db.get_bind(), plan_id)
    for x in jobs:
        _cancel_job_by_id(x.id)
    if not jobs and not plans:
        raise HTTPException(status_code=409, detail="Nothing left to cancel")
    parts = []
    if running:
        parts.append(f"{running} running")
    if waiting:
        parts.append(f"{waiting} waiting")
    msg = f"Stopped {' and '.join(parts)} job{'' if running + waiting == 1 else 's'}" if parts else "Stopped the remaining steps"
    journeys.refresh(db, j.id)
    return {"ok": True, "msg": msg, "running": running, "waiting": waiting, "plans": len(plans)}


@router.post("/{journey_id}/retry")
def retry(journey_id: int, db: Session = Depends(get_db)):
    j = _get(db, journey_id)
    step = _current(db, j)
    if step["status"] not in ("failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"{step['label']} is {step['status']}, nothing to retry")
    ref = step["ref"]
    if ref["type"] == "plan":
        from ..services import pipeline

        return {"ok": True, "msg": pipeline.retry_plan(db.get_bind(), ref["id"])}
    if ref["type"] == "media_job":
        from ..services.media import store

        job = db.query(MediaJob).filter(MediaJob.id == ref["id"]).first()
        if store.get_active_job_for_file(job.file_id) is not None:
            raise HTTPException(status_code=409, detail="File already queued or processing")
        new_id = store.create_job(job.file_id, job.job_type, job.target_resolution or "original",
                                  job.episode_count or 0, journey_id=j.id, reason=job.reason)
        store.set_file_status(job.file_id, "queued")
        return {"ok": True, "msg": f"{step['label']} queued again", "job_id": new_id}

    dl = db.query(Download).filter(Download.id == ref["id"]).first()
    if dl is None:
        raise HTTPException(status_code=404, detail="Download not found")
    if dl.watchlist_id:
        # Watchlist episode: search for the next-best release, like the automatic retry.
        from ..services.download_events import retry_download

        settings = {s.key: s.value for s in db.query(Setting).all()}
        settings["retry_enabled"] = "true"  # an explicit click overrides the auto-retry toggle
        msg = retry_download(db, dl, settings, reason="manual retry")
        return {"ok": bool(msg) and not msg.startswith(("Gave up", "No alternative", "Could not")),
                "msg": msg or "Nothing to retry"}
    if not dl.magnet_link:
        raise HTTPException(status_code=409, detail="No magnet link stored for this download")
    from ..services.qbittorrent import add_torrent

    settings = {s.key: s.value for s in db.query(Setting).all()}
    try:
        info_hash = add_torrent(dl.magnet_link, dl.download_path or "/downloads",
                                settings.get("qbit_category", "mediamate"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"qBittorrent: {exc}")
    dl.torrent_hash = info_hash or dl.torrent_hash
    dl.status = "downloading"
    dl.qbit_removed = False
    dl.notified_failed = False
    dl.notified_stalled = False
    dl.completion_first_seen_at = None
    dl.last_progress_at = datetime.now(timezone.utc)
    dl.retry_count = (dl.retry_count or 0) + 1
    db.commit()
    return {"ok": True, "msg": "Download restarted"}


@router.delete("/{journey_id}")
def remove(journey_id: int, db: Session = Depends(get_db)):
    """Remove a finished journey (and its download record) from the list."""
    j = _get(db, journey_id)
    if j.status == "active":
        raise HTTPException(status_code=409, detail="Cancel it first")
    _delete_journey(db, j)
    db.commit()
    journeys.publish_stats(db)
    return {"ok": True}


@router.post("/clear-finished")
def clear_finished(db: Session = Depends(get_db)):
    """Remove every journey that is no longer active (subsumes Downloads → Clear finished)."""
    rows = db.query(Journey).filter(Journey.status != "active").all()
    for j in rows:
        _delete_journey(db, j)
    db.commit()
    journeys.publish_stats(db)
    return {"ok": True, "cleared": len(rows)}


def _delete_journey(db: Session, j: Journey) -> None:
    from ..services import events

    # Only called for journeys that aren't active, so every job here is finished.
    # Delete rather than unlink them, or startup backfill would resurrect the journey.
    db.query(MediaJob).filter(MediaJob.journey_id == j.id).delete(synchronize_session=False)
    db.query(PipelinePlan).filter(PipelinePlan.journey_id == j.id).delete(synchronize_session=False)
    if j.download_id:
        dl = db.query(Download).filter(Download.id == j.download_id).first()
        j.download_id = None
        db.flush()
        if dl is not None:
            db.delete(dl)
    db.delete(j)
    events.publish("journey.removed", {"id": j.id})
