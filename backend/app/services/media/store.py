"""Media engine persistence — replaces FileSplitter's db.py.

Same function names, arguments and return shapes as the original, so scanner.py,
worker.py and the router port across unchanged. Rows come back as SQLAlchemy
RowMappings, which support row["col"] and dict(row) exactly like sqlite3.Row.

Tables live in MediaMate's database (models.MediaFile / models.MediaJob, created
by run_migrations); settings share MediaMate's `settings` table.
"""
import os
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import text

from ... import database
from .. import events, journeys

# Tests point this at their own engine; production uses MediaMate's.
engine = None


def _engine():
    return engine or database.engine


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Conn:
    """Thin adapter giving SQLAlchemy connections sqlite3's execute(sql, params) shape."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params: dict | None = None):
        return self._conn.execute(text(sql), params or {})


@contextmanager
def connect():
    with _engine().begin() as conn:  # commits on success, rolls back on error
        yield _Conn(conn)


def _one(result):
    return result.mappings().fetchone()


def _all(result):
    return result.mappings().fetchall()


# --- Startup recovery ---

def recover_interrupted() -> None:
    """Reset state left behind by a crash or restart (ported from FileSplitter's init_db)."""
    with connect() as conn:
        conn.execute(
            "UPDATE media_jobs SET status='error', log_tail='Interrupted by restart', finished_at=:now"
            " WHERE status='running'",
            {"now": _now()},
        )
        conn.execute("UPDATE media_files SET status='pending' WHERE status='processing'")
        # Files stuck as 'queued' with no active job can't be re-queued otherwise.
        conn.execute("""
            UPDATE media_files SET status='pending'
            WHERE status='queued'
            AND id NOT IN (
                SELECT DISTINCT file_id FROM media_jobs
                WHERE status IN ('queued','running')
            )
        """)


# --- Files ---

def upsert_file(path: str, filename: str, size_bytes: int, duration_sec: float,
                codec: str, is_anthology: bool, width: int = 0, height: int = 0,
                anthology_override: int | None = -1) -> int:
    now = _now()
    p = {"path": path, "filename": filename, "size": size_bytes, "dur": duration_sec,
         "codec": codec, "anth": int(is_anthology), "w": width, "h": height, "now": now}
    with connect() as conn:
        existing = _one(conn.execute("SELECT id, status FROM media_files WHERE path = :path", p))
        if existing:
            if anthology_override == -1:
                # Sentinel: don't touch anthology_override column
                conn.execute(
                    """UPDATE media_files SET filename=:filename, size_bytes=:size, duration_sec=:dur,
                       codec=:codec, is_anthology=:anth, width=:w, height=:h, updated_at=:now
                       WHERE path=:path""",
                    p,
                )
            else:
                conn.execute(
                    """UPDATE media_files SET filename=:filename, size_bytes=:size, duration_sec=:dur,
                       codec=:codec, is_anthology=:anth, width=:w, height=:h,
                       anthology_override=:ovr, updated_at=:now WHERE path=:path""",
                    {**p, "ovr": anthology_override},
                )
            return existing["id"]
        cur = conn.execute(
            """INSERT INTO media_files (path, filename, size_bytes, duration_sec,
               codec, is_anthology, width, height, anthology_override, status, discovered_at, updated_at)
               VALUES (:path, :filename, :size, :dur, :codec, :anth, :w, :h, NULL, 'pending', :now, :now)""",
            p,
        )
        return cur.lastrowid


def set_file_status(file_id: int, status: str, error_msg: str = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE media_files SET status=:s, error_msg=:e, updated_at=:now WHERE id=:id",
            {"s": status, "e": error_msg, "now": _now(), "id": file_id},
        )


def set_anthology_override(file_id: int, override: bool | None) -> None:
    """Set or clear the manual anthology override for a file.
    override=True/False: force value and mark as manually set.
    override=None: clear override, recalculate from stored size/duration/filename."""
    with connect() as conn:
        if override is None:
            row = _one(conn.execute(
                "SELECT filename, size_bytes, duration_sec FROM media_files WHERE id=:id",
                {"id": file_id},
            ))
            if row is None:
                return
            from . import scanner as _scanner
            auto = _scanner._is_anthology(row["filename"], row["size_bytes"] or 0, row["duration_sec"] or 0)
            conn.execute(
                "UPDATE media_files SET anthology_override=NULL, is_anthology=:a, updated_at=:now WHERE id=:id",
                {"a": int(auto), "now": _now(), "id": file_id},
            )
        else:
            conn.execute(
                "UPDATE media_files SET anthology_override=:o, is_anthology=:o, updated_at=:now WHERE id=:id",
                {"o": int(override), "now": _now(), "id": file_id},
            )


def get_file_by_path(path: str):
    with connect() as conn:
        return _one(conn.execute("SELECT * FROM media_files WHERE path=:p", {"p": path}))


def get_file(file_id: int):
    with connect() as conn:
        return _one(conn.execute("SELECT * FROM media_files WHERE id=:id", {"id": file_id}))


def get_active_job_for_file(file_id: int):
    with connect() as conn:
        return _one(conn.execute(
            "SELECT * FROM media_jobs WHERE file_id=:id AND status IN ('queued','running')"
            " ORDER BY id DESC LIMIT 1",
            {"id": file_id},
        ))


def get_job(job_id: int):
    with connect() as conn:
        return _one(conn.execute(
            "SELECT j.*, f.filename, f.path FROM media_jobs j JOIN media_files f ON j.file_id = f.id"
            " WHERE j.id=:id",
            {"id": job_id},
        ))


def get_all_files():
    with connect() as conn:
        return _all(conn.execute("SELECT * FROM media_files ORDER BY updated_at DESC"))


def get_stats() -> dict:
    with connect() as conn:
        row = _one(conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as done,
                SUM(CASE WHEN status='processing' THEN 1 ELSE 0 END) as processing,
                SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END) as queued,
                SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as errors,
                SUM(CASE WHEN status='skipped' THEN 1 ELSE 0 END) as skipped,
                SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) as ok
            FROM media_files
        """))
        saved_row = _one(conn.execute("SELECT value FROM settings WHERE key='total_saved_bytes'"))
        result = dict(row) if row else {}
        result["total_saved_bytes"] = int(saved_row["value"]) if saved_row else 0
        return result


# --- Jobs ---

def create_job(file_id: int, job_type: str, target_resolution: str = "original",
               episode_count: int = 0, journey_id: int | None = None,
               reason: str | None = None) -> int:
    if job_type == "encode" and (target_resolution or "original") == "original":
        # Folders with "shrink" on (e.g. Bellesa, 720p) encode bigger files down to their target.
        try:
            from . import targets
            row = get_file(file_id)
            if row is not None:
                target_resolution = targets.check(row).shrink_to
        except Exception:
            target_resolution = "original"
    if journey_id is None:
        journey_id = journeys.on_media_job_created(_engine(), file_id)
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO media_jobs (file_id, job_type, status, progress_pct, target_resolution,
               episode_count, saved_bytes, journey_id, reason)
               VALUES (:f, :t, 'queued', 0, :r, :e, 0, :j, :why)""",
            {"f": file_id, "t": job_type, "r": target_resolution, "e": episode_count, "j": journey_id,
             "why": reason},
        )
        job_id = cur.lastrowid
    journeys.on_media_job_changed(_engine(), journey_id)
    return job_id


def _job_meta(job_id: int) -> tuple[int | None, str | None]:
    """(journey_id, status) for a job."""
    with connect() as conn:
        row = _one(conn.execute("SELECT journey_id, status FROM media_jobs WHERE id=:id", {"id": job_id}))
        return (row["journey_id"], row["status"]) if row else (None, None)


def _journey_of(job_id: int) -> int | None:
    return _job_meta(job_id)[0]


def get_active_jobs():
    with connect() as conn:
        return _all(conn.execute("""
            SELECT j.*, f.filename, f.path, f.codec, f.size_bytes
            FROM media_jobs j JOIN media_files f ON j.file_id = f.id
            WHERE j.status IN ('queued', 'running')
            ORDER BY j.id
        """))


def purge_missing_files() -> int:
    """Delete records for files whose path no longer exists on disk. Returns count removed."""
    with connect() as conn:
        rows = _all(conn.execute("SELECT id, path FROM media_files"))
        missing_ids = [r["id"] for r in rows if not os.path.exists(r["path"])]
        if not missing_ids:
            return 0
        params = {f"i{n}": v for n, v in enumerate(missing_ids)}
        placeholders = ",".join(f":{k}" for k in params)
        conn.execute(f"DELETE FROM media_jobs WHERE file_id IN ({placeholders})", params)
        conn.execute(f"DELETE FROM media_files WHERE id IN ({placeholders})", params)
    journeys.prune_empty(_engine())
    return len(missing_ids)


def delete_file(file_id: int) -> None:
    """Remove a single file record and its associated job rows."""
    with connect() as conn:
        conn.execute("DELETE FROM media_jobs WHERE file_id=:id", {"id": file_id})
        conn.execute("DELETE FROM media_files WHERE id=:id", {"id": file_id})
    journeys.prune_empty(_engine())


def rename_file(file_id: int, new_path: str, new_filename: str) -> None:
    """Update a file's path/filename after an on-disk rename, bumping updated_at."""
    with connect() as conn:
        conn.execute(
            "UPDATE media_files SET path=:p, filename=:f, updated_at=:now WHERE id=:id",
            {"p": new_path, "f": new_filename, "now": _now(), "id": file_id},
        )


def clear_finished_jobs() -> int:
    with connect() as conn:
        cleared = conn.execute("DELETE FROM media_jobs WHERE status IN ('done','error','cancelled')").rowcount
    journeys.prune_empty(_engine())
    return cleared


def get_recent_jobs(limit: int = 20):
    with connect() as conn:
        return _all(conn.execute("""
            SELECT j.*, f.filename, f.path
            FROM media_jobs j JOIN media_files f ON j.file_id = f.id
            ORDER BY j.id DESC LIMIT :n
        """, {"n": limit}))


def update_file_size(file_id: int, size_bytes: int, codec: str = None,
                     width: int = None, height: int = None,
                     duration_sec: float = None) -> None:
    """Update size (and optionally codec/width/height/duration) after a transcode, bumping updated_at."""
    sets = ["size_bytes=:size"]
    params: dict = {"size": size_bytes}
    if codec:
        sets.append("codec=:codec")
        params["codec"] = codec
    if width is not None:
        sets.append("width=:w")
        params["w"] = width
    if height is not None:
        sets.append("height=:h")
        params["h"] = height
    if duration_sec is not None:
        sets.append("duration_sec=:dur")
        params["dur"] = duration_sec
    sets.append("updated_at=:now")
    params["now"] = _now()
    params["id"] = file_id
    with connect() as conn:
        conn.execute(f"UPDATE media_files SET {', '.join(sets)} WHERE id=:id", params)


def update_job_progress(job_id: int, progress_pct: float, log_tail: str = None,
                        saved_bytes: int = None) -> None:
    with connect() as conn:
        if saved_bytes is not None:
            conn.execute(
                "UPDATE media_jobs SET progress_pct=:p, log_tail=:l, saved_bytes=:s WHERE id=:id",
                {"p": progress_pct, "l": log_tail, "s": saved_bytes, "id": job_id},
            )
        else:
            conn.execute(
                "UPDATE media_jobs SET progress_pct=:p, log_tail=:l WHERE id=:id",
                {"p": progress_pct, "l": log_tail, "id": job_id},
            )
    journey_id, status = _job_meta(job_id)
    if status != "running":
        # The worker writes the final message / saved_bytes after setting the
        # terminal status, so this is the write that completes the journey step.
        journeys.on_media_job_changed(_engine(), journey_id)
        return
    key = f"job:{job_id}"
    if events.bus.ready(key):
        events.publish("progress", {
            "key": key, "journey_id": journey_id, "job_id": job_id,
            "progress": round(progress_pct or 0, 1), "log": log_tail,
        })


def set_job_stats(job_id: int, encoder: str, media_seconds: float | None) -> None:
    """Record which encoder a job used and how many seconds of video it processed."""
    with connect() as conn:
        conn.execute("UPDATE media_jobs SET encoder=:e, media_seconds=:m WHERE id=:id",
                     {"e": encoder, "m": media_seconds, "id": job_id})


def set_job_status(job_id: int, status: str) -> None:
    col = "started_at" if status == "running" else "finished_at"
    with connect() as conn:
        conn.execute(
            f"UPDATE media_jobs SET status=:s, {col}=:now WHERE id=:id",
            {"s": status, "now": _now(), "id": job_id},
        )
    journeys.on_media_job_changed(_engine(), _journey_of(job_id))
    if status in ("done", "error", "cancelled"):
        from .. import pipeline  # late import: pipeline imports this module
        pipeline.on_job_finished(_engine(), job_id, status)


def dequeue_next_job():
    """Atomically claim the next queued job. Safe for concurrent callers."""
    with connect() as conn:
        row = _one(conn.execute("SELECT id FROM media_jobs WHERE status='queued' ORDER BY id LIMIT 1"))
        if row is None:
            return None
        # Conditional UPDATE guards against concurrent dequeue — only one
        # caller's UPDATE will find status='queued' and win.
        updated = conn.execute(
            "UPDATE media_jobs SET status='running', started_at=:now WHERE id=:id AND status='queued'",
            {"now": _now(), "id": row["id"]},
        ).rowcount
        if updated == 0:
            return None
        job = _one(conn.execute("SELECT * FROM media_jobs WHERE id=:id", {"id": row["id"]}))
    journeys.on_media_job_changed(_engine(), job["journey_id"])
    return job


# --- Settings (MediaMate's shared settings table) ---

def get_setting(key: str, default: str = "") -> str:
    with connect() as conn:
        row = _one(conn.execute("SELECT value FROM settings WHERE key=:k", {"k": key}))
        return row["value"] if row and row["value"] is not None else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (:k, :v) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            {"k": key, "v": value},
        )


def get_settings(keys) -> dict[str, str]:
    with connect() as conn:
        params = {f"k{n}": k for n, k in enumerate(keys)}
        if not params:
            return {}
        placeholders = ",".join(f":{k}" for k in params)
        rows = _all(conn.execute(f"SELECT key, value FROM settings WHERE key IN ({placeholders})", params))
        return {r["key"]: r["value"] for r in rows if r["value"] is not None}


def increment_saved_bytes(amount: int) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('total_saved_bytes', :s) "
            "ON CONFLICT(key) DO UPDATE SET value="
            "  CAST(CAST(value AS INTEGER) + :n AS TEXT)",
            {"s": str(amount), "n": amount},
        )


def set_saved_bytes(value: int) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('total_saved_bytes', :s) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            {"s": str(value)},
        )
