"""Library previews: a still-frame thumbnail per file, and on-the-fly conversion so
any file plays in a browser (MKV, HEVC, AC-3/DTS audio and so on).

Thumbnails are cached under <data dir>/cache/thumbs, keyed by file id, size and
modification time, so a re-encoded or renamed file gets a fresh one. A file
ffmpeg can't read leaves a ``.fail`` marker, so it isn't retried on every view.
"""
import logging
import os
import subprocess
import threading
from pathlib import Path

from . import encoder

logger = logging.getLogger(__name__)

THUMB_WIDTH = 320
THUMB_TIMEOUT_S = 20
MAX_THUMB_JOBS = 2      # ffmpeg runs for thumbnails at once
MAX_PLAYERS = 2         # conversions streaming at once

_thumb_slots = threading.BoundedSemaphore(MAX_THUMB_JOBS)
_player_slots = threading.BoundedSemaphore(MAX_PLAYERS)


def cache_dir() -> Path:
    """<data dir>/cache/thumbs: next to the SQLite database (/app/data in the container)."""
    from ...database import DATABASE_URL

    db_path = DATABASE_URL.split("sqlite:///", 1)[-1] if DATABASE_URL.startswith("sqlite:///") else ""
    base = Path(db_path).parent if db_path else Path("/app/data")
    return base / "cache" / "thumbs"


def thumb_time(duration_sec: float | None) -> float:
    """Where to grab the frame: 10% in (past intros and black), at most 5 minutes."""
    if not duration_sec or duration_sec <= 0:
        return 1.0
    return round(min(duration_sec * 0.1, 300.0), 2)


def thumb_cmd(src: str, dest: str, at: float) -> list[str]:
    return ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(at), "-i", src,
            "-frames:v", "1", "-vf", f"scale={THUMB_WIDTH}:-2", "-q:v", "5", dest]


def thumbnail(file_id: int, path: str, duration_sec: float | None) -> Path | None:
    """The cached thumbnail for this file, making it if needed; None if ffmpeg can't."""
    st = os.stat(path)
    folder = cache_dir()
    stem = f"{file_id}-{st.st_size}-{int(st.st_mtime)}"
    jpg, fail = folder / f"{stem}.jpg", folder / f"{stem}.fail"
    if jpg.is_file():
        return jpg
    if fail.exists():
        return None
    folder.mkdir(parents=True, exist_ok=True)
    with _thumb_slots:
        if jpg.is_file():          # made by another request while this one waited
            return jpg
        tmp = folder / f"{stem}.tmp.jpg"
        try:
            # Short files: the 10% point can land past the last keyframe; fall back to 0.
            for at in dict.fromkeys((thumb_time(duration_sec), 0.0)):
                subprocess.run(thumb_cmd(path, str(tmp), at), capture_output=True,
                               timeout=THUMB_TIMEOUT_S, check=False)
                if tmp.is_file() and tmp.stat().st_size > 0:
                    os.replace(tmp, jpg)
                    _drop_old(folder, file_id, keep=jpg.name)
                    return jpg
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.info("Thumbnail failed for %s: %s", path, exc)
        finally:
            tmp.unlink(missing_ok=True)
        fail.touch()
        return None


def frame(file_id: int, path: str, at: float) -> Path | None:
    """A still frame at `at` seconds (JPEG, cached), for picking teaser moments."""
    st = os.stat(path)
    folder = cache_dir().parent / "frames"
    jpg = folder / f"{file_id}-{st.st_size}-{int(st.st_mtime)}-{int(at)}.jpg"
    if jpg.is_file():
        return jpg
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f"{jpg.stem}.tmp.jpg"
    with _thumb_slots:
        try:
            subprocess.run(thumb_cmd(path, str(tmp), float(int(at))), capture_output=True,
                           timeout=THUMB_TIMEOUT_S, check=False)
            if tmp.is_file() and tmp.stat().st_size > 0:
                os.replace(tmp, jpg)
                return jpg
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.info("Frame failed for %s at %s: %s", path, at, exc)
        finally:
            tmp.unlink(missing_ok=True)
    return None


def _drop_old(folder: Path, file_id: int, keep: str) -> None:
    """Remove this file's thumbnails from before it changed."""
    for old in folder.glob(f"{file_id}-*"):
        if old.name != keep:
            old.unlink(missing_ok=True)


def play_cmd(src: str, start: float, nvenc: bool | None = None) -> list[str]:
    """ffmpeg converting `src` from `start` to H.264/AAC fragmented MP4 on stdout,
    at most 720p, which every current browser plays."""
    use_nvenc = encoder.NVENC_AVAILABLE if nvenc is None else nvenc
    video = (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"] if use_nvenc
             else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"])
    cmd = ["ffmpeg", "-loglevel", "error", "-nostdin"]
    if start > 0:
        cmd += ["-ss", str(round(start, 2))]
    return cmd + [
        "-i", src,
        "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
        "-vf", "scale=-2:'min(720,ih)'", "-pix_fmt", "yuv420p", *video,
        "-c:a", "aac", "-ac", "2", "-b:a", "160k",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "pipe:1",
    ]


class Busy(Exception):
    """Too many conversions are already playing."""


class Player:
    """One running conversion. Read with ``chunks()``; ``stop()`` ends ffmpeg from any
    thread (used when the browser goes away), which also ends ``chunks()``."""

    def __init__(self, src: str, start: float):
        if not _player_slots.acquire(blocking=False):
            raise Busy(f"{MAX_PLAYERS} previews are already converting. Close one and try again.")
        try:
            self.proc = subprocess.Popen(play_cmd(src, start), stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        except OSError:
            _player_slots.release()
            raise
        self._released = False
        self._lock = threading.Lock()

    def chunks(self):
        try:
            while True:
                data = self.proc.stdout.read(64 * 1024)
                if not data:
                    break
                yield data
        finally:
            self.stop()

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        with self._lock:
            if self._released:
                return
            self._released = True
        self.proc.wait()
        _player_slots.release()


def open_player(src: str, start: float) -> Player:
    return Player(src, start)
