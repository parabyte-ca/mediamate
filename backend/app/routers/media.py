"""Media engine API — FileSplitter's /api routes, ported to FastAPI under /api/media.

Response bodies keep FileSplitter's shapes ({"ok": ..., "msg": ...} and so on) so the
Library page can be built against the same contract.
"""
import json
import os
import threading

from fastapi import APIRouter, Body
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.concurrency import iterate_in_threadpool

from ..services.media import config, encoder, preview, scanner, scene_namer, trash, worker
from ..services.media import store as db

router = APIRouter(prefix="/media", tags=["media"])

_scan_lock = threading.Lock()
_scan_status = {"running": False}


def _err(msg: str, status: int) -> JSONResponse:
    return JSONResponse({"ok": False, "msg": msg}, status_code=status)


# --- Files ---

@router.get("/files")
def list_files():
    from ..services.media import targets
    try:
        tfolders = targets.load_folders()
    except Exception:
        tfolders = None
    result = []
    for r in db.get_all_files():
        d = dict(r)
        d["category"] = config.categorize(d["path"])
        d["collections"] = json.loads(d.get("collections") or "[]")
        d.pop("plex_seen", None)
        # Compared with its folder's target: "above" (bigger, H.265) / "below" (lower than preferred).
        d["target_note"] = None
        d["target"] = None
        if tfolders is not None:
            c = targets.check(r, tfolders)
            d["target"] = c.target.describe()
            if c.meets and c.above:
                d["target_note"] = "above"
            elif c.below:
                d["target_note"] = "below"
        result.append(d)
    return result


@router.get("/files/{file_id}/stream")
def stream_file(file_id: int, download: str | None = None):
    """Serve the raw media file for in-browser preview (Range-enabled) or download."""
    row = db.get_file(file_id)
    if row is None:
        return _err("File not found", 404)
    path = row["path"]
    # Defensive: only serve files that live under a configured media root.
    if trash._media_root_for(path) is None or not os.path.isfile(path):
        return _err("File not found", 404)
    disposition = "attachment" if download == "1" else "inline"
    return FileResponse(path, filename=row["filename"], content_disposition_type=disposition)


def _servable(file_id: int):
    """The file's row if it exists and lives under a configured media root, else None."""
    row = db.get_file(file_id)
    if row is None:
        return None
    path = row["path"]
    if trash._media_root_for(path) is None or not os.path.isfile(path):
        return None
    return row


def _organise_plan(path: str, override: str | None = None) -> dict:
    """Where a file would go (regular or Mature folder, and why). Raises OrganiseError."""
    from ..database import SessionLocal
    from ..services import organise
    with SessionLocal() as s:
        return organise.plan(s, path, override=override)


def _organise(row, override: str | None = None) -> str:
    from ..services import organise
    where = _organise_plan(row["path"], override)
    return organise.organise_file(where["settings"], row["id"], where["show_hint"], where["audience"])


@router.get("/files/{file_id}/organise")
def organise_preview(file_id: int, audience: str | None = None):
    """Where Rename & organise would put this file (optionally for a chosen audience)."""
    from ..services import organise
    row = _servable(file_id)
    if row is None:
        return _err("File not found", 404)
    try:
        p = _organise_plan(row["path"], audience)
    except organise.OrganiseError as exc:
        return {"ok": False, "msg": str(exc)}
    return {"ok": True, "target": p["target"], "kind": p["kind"], "audience": p["audience"],
            "reason": p["reason"], "mature_folders": organise.mature_folders_set(p["settings"]),
            "already": os.path.abspath(p["target"]) == os.path.abspath(row["path"])}


@router.post("/files/{file_id}/organise")
def organise_one(file_id: int, body: dict = Body(default={})):
    from ..services import organise
    row = db.get_file(file_id)
    if row is None:
        return _err("File not found", 404)
    if db.get_active_job_for_file(file_id) is not None or row["status"] in ("queued", "processing"):
        return _err("This file is being processed; try again when it's done", 409)
    try:
        dest = _organise(row, body.get("audience"))
    except (organise.OrganiseError, OSError) as exc:
        return _err(str(exc), 400)
    return {"ok": True, "msg": f"Moved to {dest}", "path": dest}


@router.get("/files/{file_id}/thumb")
def file_thumbnail(file_id: int):
    """A still frame from about 10% into the file (JPEG, cached)."""
    row = _servable(file_id)
    if row is None:
        return _err("File not found", 404)
    jpg = preview.thumbnail(file_id, row["path"], row["duration_sec"])
    if jpg is None:
        return _err("No thumbnail for this file", 404)
    return FileResponse(jpg, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400"})


@router.get("/files/{file_id}/play")
def play_file(file_id: int, start: float = 0.0):
    """The file converted on the fly to H.264/AAC MP4, for files the browser can't play.
    `start` (seconds) seeks, since the converted stream has no timeline to seek in."""
    row = _servable(file_id)
    if row is None:
        return _err("File not found", 404)
    try:
        player = preview.open_player(row["path"], max(0.0, start))
    except preview.Busy as exc:
        return _err(str(exc), 429)
    except OSError:
        return _err("ffmpeg is not available", 500)

    async def body():
        # Stop ffmpeg as soon as the browser stops listening (closed, seeked, paused away).
        try:
            async for chunk in iterate_in_threadpool(player.chunks()):
                yield chunk
        finally:
            player.stop()

    return StreamingResponse(body(), media_type="video/mp4", headers={"Cache-Control": "no-store"})


@router.post("/files/{file_id}/rename")
def rename_file(file_id: int, body: dict = Body(default={})):
    row = db.get_file(file_id)
    if row is None:
        return _err("File not found", 404)
    if db.get_active_job_for_file(file_id) is not None:
        return _err("Cannot rename while a job is active", 409)

    raw_name = (body.get("new_name") or "").strip()
    old_path = row["path"]
    directory = os.path.dirname(old_path)
    ext = os.path.splitext(old_path)[1]

    # Sanitize; strip a video extension the user typed so we keep the original one (only a
    # real extension: "Rodriguez 03.07" keeps its ".07"). Check for blank first: sanitize()
    # falls back to "Scene" for empty input, which in FileSplitter silently renamed the
    # file to Scene.<ext>.
    from ..services.organise import keep_extension
    stem = keep_extension(raw_name, "")
    base = scene_namer.sanitize(stem) if stem.strip() else ""
    if not base:
        return _err("Invalid name", 400)
    new_filename = base + ext
    new_path = os.path.join(directory, new_filename)

    if new_path == old_path:
        return {"ok": True, "msg": "No change"}
    if os.path.exists(new_path):
        return _err("A file with that name already exists", 409)

    try:
        os.rename(old_path, new_path)
    except OSError as exc:
        return _err(f"Rename failed: {exc}", 500)
    db.rename_file(file_id, new_path, new_filename)
    return {"ok": True, "filename": new_filename}


@router.post("/files/{file_id}/delete")
def delete_file(file_id: int):
    row = db.get_file(file_id)
    if row is None:
        return _err("File not found", 404)
    if db.get_active_job_for_file(file_id) is not None:
        return _err("Cannot delete while a job is active", 409)

    path = row["path"]
    if os.path.exists(path):
        try:
            trash.move_to_trash(path)
        except OSError as exc:
            return _err(f"Delete failed: {exc}", 500)
    db.delete_file(file_id)
    return {"ok": True}


@router.post("/files/{file_id}/anthology")
def set_anthology(file_id: int, body: dict = Body(default={})):
    raw = body.get("value")  # True, False, or None (reset to auto)
    override = None if raw is None else bool(raw)
    if db.get_file(file_id) is None:
        return _err("File not found", 404)
    db.set_anthology_override(file_id, override)
    return {"ok": True}


@router.post("/files/{file_id}/skip")
def skip_file(file_id: int):
    file_row = db.get_file(file_id)
    if file_row is None:
        return _err("File not found", 404)
    if file_row["status"] in ("processing", "queued"):
        return _err("Cannot skip a file that is queued or processing", 409)
    db.set_file_status(file_id, "skipped")
    return {"ok": True}


@router.post("/files/{file_id}/cancel")
def cancel_file(file_id: int):
    job = db.get_active_job_for_file(file_id)
    if job is None:
        return _err("No active job for this file", 404)
    return _cancel_job_by_id(job["id"])


@router.post("/files/purge")
def purge_missing():
    return {"ok": True, "removed": db.purge_missing_files()}


# --- Jobs & queue ---

@router.get("/jobs")
def list_jobs():
    return [dict(r) for r in db.get_recent_jobs(50)]


def _cancel_job_by_id(job_id: int):
    """Shared cancel logic used by both cancel endpoints."""
    job = db.get_job(job_id)
    if job is None:
        return _err("Job not found", 404)
    status = job["status"]
    if status == "running":
        worker.cancel_job(job_id)
        return {"ok": True, "msg": "Cancel signal sent — job will stop shortly"}
    if status == "queued":
        db.set_job_status(job_id, "cancelled")
        db.set_file_status(job["file_id"], "pending")
        return {"ok": True, "msg": "Queued job cancelled"}
    return _err(f"Job is '{status}', cannot cancel", 409)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    return _cancel_job_by_id(job_id)


@router.post("/jobs/clear")
def clear_jobs():
    return {"ok": True, "cleared": db.clear_finished_jobs()}


@router.post("/queue")
def queue_job(body: dict = Body(default={})):
    file_id = body.get("file_id")
    job_type = body.get("job_type")  # encode | split | trim
    target_resolution = body.get("target_resolution", "original")
    try:
        episode_count = int(body.get("episode_count", 0) or 0)
    except (TypeError, ValueError):
        return _err("Invalid parameters", 400)

    if not file_id or job_type not in ("encode", "split", "trim"):
        return _err("Invalid parameters", 400)
    if target_resolution != "original" and target_resolution not in config.RESOLUTION_MAP:
        return _err("Invalid parameters", 400)

    if db.get_file(file_id) is None:
        return _err("File not found", 404)
    if db.get_active_job_for_file(file_id) is not None:
        return _err("File already queued or processing", 409)

    job_id = db.create_job(file_id, job_type, target_resolution, episode_count)
    db.set_file_status(file_id, "queued")
    return {"ok": True, "job_id": job_id}


@router.post("/queue/bulk")
def queue_bulk(body: dict = Body(default={})):
    """Queue the same job for many files (Library bulk bar / "Queue all")."""
    ids = body.get("file_ids") or []
    job_type = body.get("job_type")
    target_resolution = body.get("target_resolution", "original")
    try:
        episode_count = int(body.get("episode_count", 0) or 0)
    except (TypeError, ValueError):
        return _err("Invalid parameters", 400)
    if not ids or job_type not in ("encode", "split", "trim"):
        return _err("Invalid parameters", 400)
    if target_resolution != "original" and target_resolution not in config.RESOLUTION_MAP:
        return _err("Invalid parameters", 400)
    queued, skipped = [], 0
    for fid in ids:
        row = db.get_file(int(fid))
        if row is None or row["status"] in ("queued", "processing") or db.get_active_job_for_file(row["id"]):
            skipped += 1
            continue
        queued.append(db.create_job(row["id"], job_type, target_resolution, episode_count))
        db.set_file_status(row["id"], "queued")
    return {"ok": True, "queued": len(queued), "skipped": skipped, "job_ids": queued}


# --- Stars and collections (synced with Plex) ---

def _edit(file_id: int, **kw):
    from ..services import plex_sync
    try:
        return plex_sync.edit(file_id, **kw)
    except KeyError:
        return _err("File not found", 404)
    except (TypeError, ValueError) as exc:
        return _err(str(exc), 400)


@router.post("/files/{file_id}/rating")
def rate_file(file_id: int, body: dict = Body(default={})):
    stars = body.get("stars")
    return _edit(file_id, rating=None if stars in (None, "", 0) else stars)


@router.post("/files/{file_id}/collections")
def file_collections(file_id: int, body: dict = Body(default={})):
    names = body.get("names")
    if names is not None and not isinstance(names, list):
        return _err("names must be a list", 400)
    return _edit(file_id, collections=names, add=body.get("add") or [], remove=body.get("remove") or [])


@router.get("/plex/collections")
def plex_collections():
    from ..services import plex_sync
    return {"names": plex_sync.collection_names()}


@router.get("/plex/status")
def plex_status():
    from ..services import plex_sync
    return plex_sync.status()


@router.post("/plex/sync")
def plex_sync_now():
    from ..services import plex_sync
    return plex_sync.sync()


@router.post("/files/bulk")
def files_bulk(body: dict = Body(default={})):
    """Skip, delete (to .trash), rename & organise, or move to a folder (keeping the name)
    many files; busy files are left alone."""
    action = body.get("action")
    ids = body.get("file_ids") or []
    if action not in ("skip", "delete", "organise", "move", "rate", "collect") or not ids:
        return _err("Invalid parameters", 400)
    if action in ("rate", "collect"):
        return _bulk_tag(action, ids, body)
    dest_root = None
    if action == "move":
        from ..database import SessionLocal
        from ..models import DownloadPath
        from ..services import folders as folder_service
        with SessionLocal() as s:
            dp = s.get(DownloadPath, int(body.get("folder_id") or 0))
            dest_root = folder_service.local_of(s, dp) if dp else None
        if not dest_root or not os.path.isdir(dest_root):
            return _err("Pick a folder MediaMate can see", 400)
    done, skipped, errors = 0, 0, []
    for fid in ids:
        row = db.get_file(int(fid))
        if row is None or db.get_active_job_for_file(row["id"]) is not None or \
                row["status"] in ("queued", "processing"):
            skipped += 1
            continue
        if action == "skip":
            db.set_file_status(row["id"], "skipped")
        elif action == "organise":
            from ..services import organise
            try:
                _organise(row, (body.get("audiences") or {}).get(str(row["id"])))
            except (organise.OrganiseError, OSError) as exc:
                errors.append(f"{row['filename']}: {exc}")
                continue
        elif action == "move":
            from ..database import SessionLocal
            from ..services import doctor
            dest = os.path.join(dest_root, row["filename"])
            if os.path.abspath(dest) == os.path.abspath(row["path"]):
                skipped += 1
                continue
            try:
                with SessionLocal() as s:
                    doctor.move_back(s, row["id"], dest)
            except (ValueError, OSError) as exc:
                errors.append(f"{row['filename']}: {exc}")
                continue
        else:
            if os.path.exists(row["path"]):
                try:
                    trash.move_to_trash(row["path"])
                except OSError as exc:
                    errors.append(f"{row['filename']}: {exc}")
                    continue
            db.delete_file(row["id"])
        done += 1
    return {"ok": not errors, "done": done, "skipped": skipped, "errors": errors}


def _bulk_tag(action: str, ids: list, body: dict):
    """Stars or collections for many files (busy files too: nothing on disk changes)."""
    from ..services import plex_sync
    done, errors, sent = 0, [], 0
    for fid in ids:
        try:
            if action == "rate":
                stars = body.get("stars")
                r = plex_sync.edit(int(fid), rating=None if stars in (None, "", 0) else stars)
            else:
                r = plex_sync.edit(int(fid), add=body.get("add") or [], remove=body.get("remove") or [])
        except KeyError:
            continue
        except (TypeError, ValueError) as exc:
            row = db.get_file(int(fid))
            errors.append(f"{row['filename'] if row else fid}: {exc}")
            continue
        done += 1
        sent += r.get("plex") == "sent"
    return {"ok": not errors, "done": done, "skipped": 0, "errors": errors, "sent": sent}


@router.post("/queue/pause")
def pause_queue():
    worker.pause()
    db.set_setting("queue_paused", "1")
    return {"ok": True, "paused": True}


@router.post("/queue/resume")
def resume_queue():
    worker.resume()
    db.set_setting("queue_paused", "0")
    return {"ok": True, "paused": False}


# --- Scan & stats ---

@router.post("/scan")
def start_scan():
    with _scan_lock:
        if _scan_status["running"]:
            return _err("Scan already running", 409)
        _scan_status["running"] = True

    def _run():
        try:
            result = scanner.scan_all()
            if result and result.get("new_ids"):
                from ..services import pipeline
                pipeline.on_scanned(db._engine(), result["new_ids"])
        finally:
            _scan_status["running"] = False

    threading.Thread(target=_run, name="media-scan", daemon=True).start()
    return {"ok": True, "msg": "Scan started"}


@router.get("/stats")
def stats():
    return {
        **db.get_stats(),
        "paused": worker.is_paused(),
        "scan_running": _scan_status["running"],
    }


# --- Trash ---

@router.get("/trash")
def trash_stats():
    return trash.trash_stats()


@router.post("/trash/empty")
def empty_trash():
    result = trash.empty_trash()
    return {"ok": True, "freed_bytes": result["size_bytes"], "removed_files": result["file_count"]}


# --- Settings ---

@router.get("/settings")
def get_settings():
    out = {key: getattr(config, attr) for key, (attr, _cast) in config.SETTINGS_MAP.items()}
    out["split_keywords"] = ",".join(config.SPLIT_KEYWORDS)
    out["keep_audio_langs"] = ",".join(config.KEEP_AUDIO_LANGS)
    out["keep_sub_langs"] = ",".join(config.KEEP_SUB_LANGS)
    out["tpdb_api_key_set"] = bool(config.TPDB_API_KEY)
    out.pop("tpdb_api_key")  # never echo the secret back
    out.update({
        "resolutions": ["original"] + list(config.RESOLUTION_MAP.keys()),
        "nvenc_available": encoder.NVENC_AVAILABLE,
        "nvenc_probe_done": encoder.PROBE_DONE.is_set(),
        "nvenc_last_error": encoder.LAST_NVENC_ERROR,
        "cpu_presets": encoder._CPU_PRESETS,
        "nvenc_presets": encoder._NVENC_PRESETS,
    })
    return out


@router.post("/settings")
def update_settings(body: dict = Body(default={})):
    """Persist runtime-adjustable settings; they apply to the next job without a restart."""
    rejected = []
    for key, val in body.items():
        if key not in config.SETTINGS_MAP:
            continue
        if key == "media_paths":
            # Media folders are rows in the Folders list now; this keeps old callers working.
            from sqlalchemy.orm import Session
            from ..services import folders
            paths = val if isinstance(val, (list, tuple)) else config._parse_paths(str(val))
            with Session(bind=db._engine()) as s:
                folders.set_scan_paths(s, [str(p) for p in paths])
            continue
        if config.apply(key, val):
            db.set_setting(key, config.serialize(key, val))
        else:
            rejected.append(key)
    if rejected:
        return JSONResponse({"ok": False, "msg": "Invalid value", "rejected": rejected}, status_code=400)
    return {"ok": True}


@router.post("/settings/saved-bytes")
def set_saved_bytes(body: dict = Body(default={})):
    try:
        value = int(body.get("value", 0))
    except (TypeError, ValueError):
        return _err("Invalid value", 400)
    db.set_saved_bytes(value)
    return {"ok": True, "total_saved_bytes": value}


@router.post("/encoder/recheck-gpu")
def recheck_gpu():
    available = encoder.recheck_nvenc()
    return {"nvenc_available": available, "nvenc_last_error": encoder.LAST_NVENC_ERROR}
