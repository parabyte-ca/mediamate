"""Journeys — one item's trip through ScreenMate, for the Activity page.

A Journey row only records identity and links (download_id / media jobs). Its
steps are derived on demand from the rows that already hold the truth — the
Download row (plus live qBittorrent progress) and the MediaJob rows carrying its
journey_id — so they can't drift and they survive restarts.

Two hooks keep journeys current without touching any existing call site:
  * an after_flush/after_commit listener on every SQLAlchemy Session catches each
    Download insert, status change and delete (install_download_hooks);
  * services/media/store.py calls on_media_job_* as jobs are created and change.
"""
import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import event, func, inspect, text
from sqlalchemy.orm import Session

from ..models import Download, Journey, MediaJob, Setting, WatchlistItem
from . import events

logger = logging.getLogger(__name__)

_DOWNLOAD_STATUS = {
    "adding": "pending",
    "downloading": "running",
    "completed": "done",
    "seeding": "done",   # legacy
    "done": "done",      # legacy
    "failed": "failed",
    "error": "failed",
    "cancelled": "cancelled",
}
_JOB_STATUS = {
    "queued": "pending",
    "running": "running",
    "done": "done",
    "error": "failed",
    "cancelled": "cancelled",
}
_JOB_LABEL = {"encode": "Encode", "split": "Split", "trim": "Trim"}

# Latest qBittorrent figures per download id, filled by download_poller. Transient
# by design: only the persisted status matters after a restart.
_live: dict[int, dict] = {}
_live_lock = threading.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # matches func.now() columns


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def set_live(download_id: int, data: dict | None) -> None:
    with _live_lock:
        if data is None:
            _live.pop(download_id, None)
        else:
            _live[download_id] = data


def _get_live(download_id: int) -> dict:
    with _live_lock:
        return dict(_live.get(download_id, {}))


# ── Get-or-create ─────────────────────────────────────────────────────────────

def for_download(db: Session, dl: Download) -> Journey:
    j = db.query(Journey).filter(Journey.download_id == dl.id).first()
    if j is None:
        j = Journey(title=dl.title, source="download", download_id=dl.id, status="active",
                    created_at=dl.created_at or _utcnow(), updated_at=_utcnow())
        db.add(j)
        db.flush()
    return j


def for_media_file(db: Session, file_id: int) -> Journey:
    """One journey per media file (reused when the file is processed again)."""
    j = (db.query(Journey).filter(Journey.media_file_id == file_id)
         .order_by(Journey.id.desc()).first())
    if j is None:
        row = db.execute(text("SELECT filename FROM media_files WHERE id=:id"), {"id": file_id}).first()
        j = Journey(title=row[0] if row else f"File {file_id}", source="library",
                    media_file_id=file_id, status="active", updated_at=_utcnow())
        db.add(j)
        db.flush()
    return j


# ── Snapshot ──────────────────────────────────────────────────────────────────

def _stall_threshold(db: Session) -> timedelta:
    row = db.query(Setting).filter(Setting.key == "stall_hours").first()
    try:
        return timedelta(hours=max(int(row.value), 1)) if row and row.value else timedelta(hours=2)
    except ValueError:
        return timedelta(hours=2)


def _download_steps(db: Session, dl: Download) -> list[dict]:
    status = _DOWNLOAD_STATUS.get(dl.status or "", "pending")
    origin = None
    if dl.watchlist_id:
        item = db.query(WatchlistItem).filter(WatchlistItem.id == dl.watchlist_id).first()
        origin = f"Watchlist · {item.title}" if item else "Watchlist"
    found = {
        "kind": "found", "label": "Found", "status": "done",
        "detail": origin or "Added manually",
        "started_at": _iso(dl.created_at), "finished_at": _iso(dl.created_at),
        "ref": {"type": "download", "id": dl.id},
    }
    live = _get_live(dl.id) if status in ("pending", "running") else {}
    progress = live.get("progress")
    if progress is None:
        progress = 1.0 if status == "done" else (dl.last_progress or 0.0)
    ref_time = dl.last_progress_at or dl.created_at
    stale = False
    if status == "running" and ref_time is not None:
        ref_aware = ref_time if ref_time.tzinfo else ref_time.replace(tzinfo=timezone.utc)
        stale = datetime.now(timezone.utc) - ref_aware >= _stall_threshold(db)
    download = {
        "kind": "download", "label": "Download", "status": status,
        "progress": round(progress * 100, 1),
        "speed": live.get("dlspeed"), "eta": live.get("eta"),
        "size_bytes": dl.size_bytes, "path": dl.download_path,
        "detail": dl.status, "stale": stale, "retry_count": dl.retry_count or 0,
        "started_at": _iso(dl.created_at),
        "finished_at": _iso(dl.completion_first_seen_at) if status == "done" else None,
        "ref": {"type": "download", "id": dl.id},
    }
    return [found, download]


def _job_steps(db: Session, journey_id: int, with_logs: bool) -> list[dict]:
    jobs = (db.query(MediaJob).filter(MediaJob.journey_id == journey_id)
            .order_by(MediaJob.id).all())
    steps = []
    for job in jobs:
        status = _JOB_STATUS.get(job.status or "", "pending")
        step = {
            "kind": job.job_type, "label": _JOB_LABEL.get(job.job_type, job.job_type.title()),
            "status": status, "progress": round(job.progress_pct or 0, 1),
            "detail": job.log_tail if status != "running" else None,
            "saved_bytes": job.saved_bytes or 0, "reason": job.reason,
            "started_at": job.started_at, "finished_at": job.finished_at,
            "ref": {"type": "media_job", "id": job.id},
        }
        if with_logs or status == "running":
            step["log"] = job.log_tail
        steps.append(step)
    return steps


def _derive_status(steps: list[dict]) -> str:
    if any(s["status"] in ("pending", "running") for s in steps):
        return "active"
    ran = [s for s in steps if s["status"] != "skipped"]
    if not ran:
        return "done"
    last = ran[-1]["status"]
    return {"failed": "failed", "cancelled": "cancelled"}.get(last, "done")


def _current_index(steps: list[dict]) -> int:
    for i, s in enumerate(steps):
        if s["status"] in ("running", "pending", "failed"):
            return i
    # Finished: point at the last step that actually ran (not a skipped tail).
    for i in range(len(steps) - 1, -1, -1):
        if steps[i]["status"] != "skipped":
            return i
    return len(steps) - 1


def _steps(db: Session, j: Journey, with_logs: bool = False) -> list[dict]:
    steps: list[dict] = []
    if j.download_id:
        dl = db.query(Download).filter(Download.id == j.download_id).first()
        if dl is not None:
            steps += _download_steps(db, dl)
    from . import pipeline  # late import: pipeline imports this module
    return steps + _job_steps(db, j.id, with_logs) + pipeline.plan_steps(db, j.id)


def snapshot(db: Session, j: Journey, with_logs: bool = False) -> dict:
    steps = _steps(db, j, with_logs)
    poster = None
    if j.download_id:
        row = db.execute(text(
            "SELECT w.poster_url FROM downloads d JOIN watchlist w ON w.id = d.watchlist_id"
            " WHERE d.id = :id"), {"id": j.download_id}).first()
        poster = row[0] if row else None
    cur = _current_index(steps) if steps else -1
    return {
        "id": j.id, "title": j.title, "source": j.source, "status": j.status,
        "download_id": j.download_id, "media_file_id": j.media_file_id,
        "poster_url": poster,
        "created_at": _iso(j.created_at), "updated_at": _iso(j.updated_at),
        "current_step": cur,
        "saved_bytes": sum(s.get("saved_bytes") or 0 for s in steps),
        "steps": steps,
    }


# ── Refresh & stats ───────────────────────────────────────────────────────────

def _latest_time(steps: list[dict]) -> datetime | None:
    latest = None
    for s in steps:
        for key in ("finished_at", "started_at"):
            raw = s.get(key)
            if not raw:
                continue
            try:
                t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                continue
            t = t.astimezone(timezone.utc).replace(tzinfo=None) if t.tzinfo else t
            if latest is None or t > latest:
                latest = t
    return latest


def refresh(db: Session, journey_id: int, *, publish: bool = True, touch: bool = True) -> dict | None:
    """Recompute the cached status, bump updated_at, commit and broadcast the snapshot.

    touch=False (backfill/import) dates the journey by its own steps instead of now,
    so history doesn't all show up as "done today"."""
    j = db.query(Journey).filter(Journey.id == journey_id).first()
    if j is None:
        return None
    steps = _steps(db, j)
    if not steps and j.download_id is None:
        # Its download was deleted / its jobs cleared, and nothing else hangs off it.
        db.delete(j)
        db.commit()
        if publish:
            events.publish("journey.removed", {"id": journey_id})
            publish_stats(db)
        return None
    j.status = _derive_status(steps)
    j.updated_at = _utcnow() if touch else (_latest_time(steps) or j.created_at or _utcnow())
    db.commit()
    snap = snapshot(db, j)
    if publish:
        events.publish("journey.updated", snap)
        publish_stats(db)
    return snap


def stats(db: Session) -> dict:
    now = _utcnow()
    active = db.query(func.count(Journey.id)).filter(Journey.status == "active").scalar() or 0
    failed = (db.query(func.count(Journey.id))
              .filter(Journey.status == "failed", Journey.updated_at >= now - timedelta(hours=24))
              .scalar() or 0)
    local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    since = local_midnight.astimezone(timezone.utc).isoformat()
    saved_today = db.execute(text(
        "SELECT COALESCE(SUM(saved_bytes), 0) FROM media_jobs"
        " WHERE status='done' AND job_type='encode' AND finished_at >= :since"
    ), {"since": since}).scalar() or 0
    paused = db.query(Setting).filter(Setting.key == "queue_paused").first()
    return {
        "active": int(active),
        "failed_24h": int(failed),
        "saved_today_bytes": int(saved_today),
        "queue_paused": bool(paused and paused.value == "1"),
    }


def publish_stats(db: Session) -> None:
    events.publish("stats", stats(db))


# ── Backfill ──────────────────────────────────────────────────────────────────

def backfill(db: Session) -> int:
    """Create journeys for downloads and media jobs that predate journeys. Idempotent."""
    created = 0
    linked = {r[0] for r in db.query(Journey.download_id).filter(Journey.download_id.isnot(None))}
    for dl in db.query(Download).order_by(Download.id).all():
        if dl.id not in linked:
            for_download(db, dl)
            created += 1
    # Jobs imported from FileSplitter are history: they stay in the Library, not Activity.
    orphan = "journey_id IS NULL AND (reason IS NULL OR reason != :imported)"
    imported = {"imported": "Imported from FileSplitter"}
    orphan_files = [r[0] for r in db.execute(text(
        f"SELECT DISTINCT file_id FROM media_jobs WHERE {orphan}"), imported)]
    for file_id in orphan_files:
        j = for_media_file(db, file_id)
        db.execute(text(f"UPDATE media_jobs SET journey_id=:j WHERE file_id=:f AND {orphan}"),
                   {"j": j.id, "f": file_id, **imported})
        created += 1
    db.commit()
    if created:
        for j in db.query(Journey).all():
            refresh(db, j.id, publish=False, touch=False)
    return created


# ── Hooks ─────────────────────────────────────────────────────────────────────

_PENDING_KEY = "screenmate_journey_downloads"
_DONE = {"completed", "seeding", "done"}


def _after_flush(session: Session, _ctx) -> None:
    pending = session.info.setdefault(_PENDING_KEY, set())
    for obj in session.new:
        if isinstance(obj, Download):
            pending.add(("upsert", obj.id))
    for obj in session.dirty:
        if isinstance(obj, Download) and session.is_modified(obj, include_collections=False):
            pending.add(("upsert", obj.id))
            hist = inspect(obj).attrs.status.history
            if (any(v in _DONE for v in hist.added or ())
                    and not any(v in _DONE for v in hist.deleted or ())):
                pending.add(("complete", obj.id))
    for obj in session.deleted:
        if isinstance(obj, Download):
            pending.add(("delete", obj.id))


def _after_commit(session: Session) -> None:
    pending = session.info.pop(_PENDING_KEY, None)
    if not pending:
        return
    bind = session.get_bind()
    completed = sorted(dl_id for action, dl_id in pending if action == "complete" and dl_id)
    try:
        with Session(bind=bind) as db:
            for action, dl_id in sorted(pending, key=lambda p: (p[0] == "delete", p[1] or 0)):
                if dl_id is None or action == "complete":
                    continue
                if action == "delete":
                    j = db.query(Journey).filter(Journey.download_id == dl_id).first()
                    if j is not None:
                        j.download_id = None
                        db.commit()
                        refresh(db, j.id)
                    set_live(dl_id, None)
                    continue
                dl = db.query(Download).filter(Download.id == dl_id).first()
                if dl is None:
                    continue
                j = for_download(db, dl)
                db.commit()
                refresh(db, j.id)
    except Exception:
        # Journeys are a view; never let them break a download write.
        logger.exception("Journey update after commit failed")
    for dl_id in completed:
        try:
            from . import movies
            movies.on_download_complete(bind, dl_id)
        except Exception:
            logger.exception("Marking the watchlist movie finished failed (download %s)", dl_id)
        try:
            from . import music, music_library
            if music.on_download_complete(bind, dl_id):
                music_library.import_later(bind, dl_id)
        except Exception:
            logger.exception("Marking the album finished failed (download %s)", dl_id)
        _start_pipeline(bind, dl_id)


def _start_pipeline(bind, download_id: int) -> None:
    """Run the auto pipeline for a finished download off the caller's thread (it probes files)."""
    from . import pipeline

    def run():
        try:
            pipeline.on_download_complete(bind, download_id)
        except Exception:
            logger.exception("Pipeline failed for download %s", download_id)

    if PIPELINE_SYNC:
        run()
    else:
        threading.Thread(target=run, name=f"pipeline-dl-{download_id}", daemon=True).start()


# Tests set this so the pipeline runs inline and deterministically.
PIPELINE_SYNC = False


def _after_rollback(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


_hooks_installed = False


def install_download_hooks() -> None:
    global _hooks_installed
    if _hooks_installed:
        return
    event.listen(Session, "after_flush", _after_flush)
    event.listen(Session, "after_commit", _after_commit)
    event.listen(Session, "after_soft_rollback", lambda s, _t: _after_rollback(s))
    _hooks_installed = True


# Media-job hooks — called by services/media/store.py with its own engine.

def on_media_job_created(bind, file_id: int) -> int | None:
    """Return the journey id a new job for this file should attach to."""
    try:
        with Session(bind=bind) as db:
            j = for_media_file(db, file_id)
            db.commit()
            return j.id
    except Exception:
        logger.exception("Could not attach media job to a journey")
        return None


def prune_empty(bind) -> int:
    """Drop journeys left with no download and no media jobs (after jobs are cleared)."""
    try:
        with Session(bind=bind) as db:
            ids = [r[0] for r in db.execute(text(
                "SELECT id FROM journeys WHERE download_id IS NULL"
                " AND id NOT IN (SELECT journey_id FROM media_jobs WHERE journey_id IS NOT NULL)"))]
            if not ids:
                return 0
            db.query(Journey).filter(Journey.id.in_(ids)).delete(synchronize_session=False)
            db.commit()
            for jid in ids:
                events.publish("journey.removed", {"id": jid})
            publish_stats(db)
            return len(ids)
    except Exception:
        logger.exception("Pruning empty journeys failed")
        return 0


def on_media_job_changed(bind, journey_id: int | None) -> None:
    if journey_id is None:
        return
    try:
        with Session(bind=bind) as db:
            refresh(db, journey_id)
    except Exception:
        logger.exception("Journey refresh for media job failed")
