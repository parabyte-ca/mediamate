"""Thumbnails made ahead of time, in the background.

Library rows never wait for ffmpeg: a missing thumbnail is put at the front of this
queue and the row tries again a few seconds later. One ffmpeg runs at a time, at low
priority, and the worker steps aside while an encode is running or a preview is
converting, so playback and encodes always come first.

Work arrives after each library scan, nightly (anything still missing), when a row
asks for a thumbnail, and from the "Make thumbnails" buttons.
"""
import collections
import logging
import threading
import time

from . import preview, store

logger = logging.getLogger(__name__)

MAX_TRIES = 3                 # timeouts in one go before leaving it for the nightly pass
IDLE_WAIT_S = 5               # how long to step aside while encodes or previews run

_lock = threading.Lock()
_wake = threading.Event()
_queue: collections.deque[int] = collections.deque()
_queued: set[int] = set()
_tries: dict[int, int] = {}
_state = {"done": 0, "total": 0, "failed": 0, "current": None}
_thread: threading.Thread | None = None


def _push(ids, front: bool = False) -> int:
    n = 0
    with _lock:
        for fid in ids:
            if fid in _queued:
                if front:                      # asked for now: move it up
                    try:
                        _queue.remove(fid)
                    except ValueError:
                        pass
                    _queue.appendleft(fid)
                continue
            _queued.add(fid)
            (_queue.appendleft if front else _queue.append)(fid)
            _state["total"] += 1
            n += 1
    if n or front:
        _wake.set()
    return n


def ask(file_id: int) -> None:
    """A row wants this thumbnail: make it next."""
    _push([file_id], front=True)


def add(ids) -> int:
    """Queue these files (behind anything asked for)."""
    return _push(list(ids))


def missing_ids(retry_failed: bool = False) -> list[int]:
    """Files that have no thumbnail yet (and, unless retry_failed, didn't fail lately)."""
    out = []
    for row in store.get_all_files():
        path = row["path"]
        if preview.cached_thumbnail(row["id"], path):
            continue
        if not retry_failed and preview.failed_recently(row["id"], path):
            continue
        out.append(row["id"])
    return out


def queue_missing(retry_failed: bool = False) -> int:
    if retry_failed:
        preview.clear_failures()
    return add(missing_ids())


def status() -> dict:
    with _lock:
        left = len(_queue)
        return {"running": bool(left or _state["current"]), "queued": left, "done": _state["done"],
                "total": _state["total"], "failed": _state["failed"], "current": _state["current"]}


def _busy() -> bool:
    if preview.players_busy():
        return True
    try:
        return any(j["status"] == "running" for j in store.get_active_jobs())
    except Exception:
        return False


def _next() -> int | None:
    with _lock:
        if not _queue:
            if not _state["current"]:          # batch finished: start counting afresh next time
                _state.update(done=0, total=0, failed=0)
            return None
        return _queue[0]


def _work_one(fid: int) -> None:
    row = store.get_file(fid)
    with _lock:
        _state["current"] = fid
    result = "error"
    try:
        if row is not None:
            result = preview.make_thumbnail(fid, row["path"], row["duration_sec"])
    except Exception as exc:                     # never let one file stop the worker
        logger.warning("Thumbnail for file %s failed: %s", fid, exc)
    with _lock:
        _state["current"] = None
        if _queue and _queue[0] == fid:
            _queue.popleft()
        if result == "retry" and _tries.get(fid, 0) + 1 < MAX_TRIES:
            _tries[fid] = _tries.get(fid, 0) + 1
            _queue.append(fid)                   # try again after the rest
            return
        _queued.discard(fid)
        _tries.pop(fid, None)
        _state["done"] += 1
        if result != "ok":
            _state["failed"] += 1


def run_once(max_items: int | None = None) -> int:
    """Work through the queue now (tests and scripts). Returns how many were handled."""
    n = 0
    while (max_items is None or n < max_items) and (fid := _next()) is not None:
        _work_one(fid)
        n += 1
    return n


def _loop() -> None:
    while True:
        fid = _next()
        if fid is None:
            _wake.wait(60)
            _wake.clear()
            continue
        if _busy():
            time.sleep(IDLE_WAIT_S)
            continue
        _work_one(fid)


def start() -> None:
    """Start the worker, retry old "couldn't read" markers once, and queue what's missing."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="thumbnails", daemon=True)
    _thread.start()

    def first_pass():
        try:
            from ...database import SessionLocal
            from ...models import Setting
            with SessionLocal() as db:
                if not db.get(Setting, "thumbs_retry_v1"):
                    preview.clear_failures()       # made under load, many were only timeouts
                    db.merge(Setting(key="thumbs_retry_v1", value="1"))
                    db.commit()
            queue_missing()
        except Exception as exc:
            logger.warning("Couldn't queue missing thumbnails: %s", exc)
    threading.Thread(target=first_pass, name="thumbnails-first-pass", daemon=True).start()


def reset() -> None:
    """Forget the queue (tests)."""
    with _lock:
        _queue.clear()
        _queued.clear()
        _tries.clear()
        _state.update(done=0, total=0, failed=0, current=None)
