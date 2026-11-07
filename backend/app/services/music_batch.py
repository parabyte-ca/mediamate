"""Bulk actions on many music albums, run in the background with progress.

MusicBrainz allows one request a second, so finding covers or tags for many albums
can take minutes. Actions run in one background thread, one at a time; the Library
polls :func:`status` for progress.
"""
import logging
import os
import threading
from datetime import datetime, timezone

from . import music_library, musicbrainz

logger = logging.getLogger(__name__)

ACTIONS = ("covers", "fix_tags", "organise", "embed")
LABELS = {"covers": "Finding artwork", "fix_tags": "Fixing tags", "organise": "Organising for Plex",
          "embed": "Putting covers in files"}
SYNC = False                         # tests run batches inline

_lock = threading.Lock()
_state: dict = {"running": False, "action": None, "label": None, "done": 0, "total": 0,
                "changed": 0, "failed": [], "msg": None, "finished_at": None}


def status() -> dict:
    return dict(_state, failed=list(_state["failed"]))


def _files(db, album) -> list[str]:
    from ..models import MusicTrack
    return [t.path for t in db.query(MusicTrack).filter(MusicTrack.album_id == album.id).order_by(MusicTrack.path)
            if os.path.isfile(t.path)]


def _one(db, action: str, album) -> bool:
    """Do ``action`` on one album. True if something changed. Raises with a reason."""
    if not os.path.isdir(album.folder):
        raise ValueError("folder is gone; scan again")
    files = _files(db, album)
    if action == "covers":
        had = music_library.cover_in(album.folder)
        res = music_library.ensure_cover(album.folder, files, album.artist, album.title, album.mb_release_group_id)
        if res.get("unavailable"):
            raise ValueError("MusicBrainz is unavailable")
        if not res.get("cover"):
            album.cover_checked_at = datetime.now(timezone.utc)
            raise ValueError("no artwork found")
        return not had
    if action == "fix_tags":
        res = music_library.fix_tags(files, album.artist or "", album.title or "", album.mb_release_group_id)
        if not res.get("tagged"):
            raise ValueError(res.get("msg") or "not found on MusicBrainz")
        return True
    if action == "organise":
        root = music_library.music_dir(db)
        if not root or not os.path.isdir(root):
            raise ValueError("choose a music library in Settings → Folders first")
        res = music_library.organise(db, files, root, keep_source=music_library.in_download_folder(db, album.folder))
        if not res.get("ok"):
            raise ValueError(res.get("msg"))
        old = album.folder
        if os.path.normpath(res["folder"]) != os.path.normpath(old):
            music_library.scan_folder(db, old)
            music_library.scan_folder(db, os.path.normpath(res["folder"]))
            return True
        return bool(res.get("moved"))
    if action == "embed":
        if not album.cover:
            raise ValueError("no cover to put in the files")
        with open(os.path.join(album.folder, album.cover), "rb") as fh:
            data = fh.read()
        return music_library.embed_cover(files, data)["embedded"] > 0
    raise ValueError(f"unknown action {action}")


def _run(bind, action: str, ids: list[int]) -> None:
    from sqlalchemy.orm import Session
    from ..models import MusicLibAlbum
    try:
        with Session(bind=bind) as db:
            for album_id in ids:
                album = db.get(MusicLibAlbum, album_id)
                if album is not None:
                    label = f"{album.artist or '?'} – {album.title or '?'}"
                    try:
                        if _one(db, action, album):
                            _state["changed"] += 1
                        if action != "organise" and db.get(MusicLibAlbum, album_id) is not None:
                            music_library.scan_folder(db, album.folder)
                    except (ValueError, OSError, musicbrainz.MusicBrainzUnavailable) as exc:
                        _state["failed"].append(f"{label}: {exc}")
                    db.commit()
                _state["done"] += 1
            music_library.mark_duplicates(db)
            db.commit()
    except Exception:
        logger.exception("Music batch %s failed", action)
        _state["failed"].append("Stopped by an unexpected error; see the log")
    finally:
        n, ok = _state["total"], _state["changed"]
        verb = {"covers": "got artwork", "fix_tags": "tagged", "organise": "organised",
                "embed": "had covers put in their files"}[action]
        _state["msg"] = f"{ok} of {n} album{'s' if n != 1 else ''} {verb}" + (
            f"; {len(_state['failed'])} couldn't be" if _state["failed"] else "")
        _state["running"] = False
        _state["finished_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("Music batch: %s", _state["msg"])


def start(action: str, ids: list[int], bind=None) -> dict:
    """Start a batch. Raises ValueError for a bad action, RuntimeError if one is running."""
    from .media import store
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
    ids = [int(i) for i in dict.fromkeys(ids)]
    if not ids:
        raise ValueError("pick at least one album")
    with _lock:
        if _state["running"]:
            raise RuntimeError(f"{_state['label']} is still running ({_state['done']} of {_state['total']})")
        _state.update(running=True, action=action, label=LABELS[action], done=0, total=len(ids), changed=0,
                      failed=[], msg=None, finished_at=None)
    bind = bind or store._engine()
    if SYNC:
        _run(bind, action, ids)
    else:
        threading.Thread(target=_run, args=(bind, action, ids), name=f"music-batch-{action}", daemon=True).start()
    return status()
