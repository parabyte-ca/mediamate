"""Teasers: a short clip (about 30 s) stitched from a few moments of a video, for
sharing an adult video without sharing all of it.

Each moment is cut with an input seek, scaled to at most 720p, faded in and out
(picture and sound), then all of them are joined into one H.264/AAC MP4. Clips are
cached under <data dir>/cache/teasers, keyed by file id and the chosen moments.
"""
import hashlib
import json
import logging
import os
import subprocess
import threading
from pathlib import Path

from . import encoder, preview

logger = logging.getLogger(__name__)

MOMENTS = 6
CLIP_S = 5.0                # each moment
MAX_TOTAL_S = 30.0
FADE_S = 0.25
BUILD_TIMEOUT_S = 300


class TeaserError(Exception):
    pass


def cache_dir() -> Path:
    return preview.cache_dir().parent / "teasers"


def clip_len(duration: float, n: int) -> float:
    """How long each moment is: 5 s, or less for a short video."""
    if not duration or duration <= 0 or n <= 0:
        return CLIP_S
    return round(max(1.0, min(CLIP_S, MAX_TOTAL_S / n, duration / (n * 2))), 2)


def default_moments(duration: float | None, n: int = MOMENTS) -> list[float]:
    """Start times spread evenly between 10% and 90% of the video (past intros and
    credits). Short videos get fewer moments."""
    d = float(duration or 0)
    if d <= 0:
        return [0.0]
    n = max(1, min(n, int(d // (CLIP_S * 2)) or 1))
    length = clip_len(d, n)
    lo, hi = d * 0.1, max(d * 0.1, d * 0.9 - length)
    if n == 1:
        return [round(min(lo, max(0.0, d - length)), 2)]
    step = (hi - lo) / (n - 1)
    return [round(lo + i * step, 2) for i in range(n)]


def valid(starts, duration: float | None) -> list[float]:
    """Checked, sorted start times. Raises TeaserError."""
    if not isinstance(starts, list) or not 1 <= len(starts) <= MOMENTS:
        raise TeaserError(f"Pick between 1 and {MOMENTS} moments")
    out = []
    d = float(duration or 0)
    for s in starts:
        if not isinstance(s, (int, float)) or s < 0 or (d and s > max(0.0, d - 1)):
            raise TeaserError("A moment is outside the video")
        out.append(round(float(s), 2))
    return sorted(out)


def probe(path: str) -> tuple[int, bool]:
    """(index of the real video stream among video streams, has audio). Skips cover art
    (attached pictures), which some MP4s carry as their first video stream."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=codec_type:stream_disposition=attached_pic", "-of", "json", path],
                           capture_output=True, text=True, timeout=30, check=False)
        streams = json.loads(r.stdout or "{}").get("streams", [])
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return 0, False
    vids = [st for st in streams if st.get("codec_type") == "video"]
    vi = next((i for i, st in enumerate(vids) if not (st.get("disposition") or {}).get("attached_pic")), 0)
    return vi, any(st.get("codec_type") == "audio" for st in streams)


def has_audio(path: str) -> bool:
    return probe(path)[1]


def teaser_cmd(src: str, starts: list[float], length: float, dest: str,
               audio: bool = True, nvenc: bool | None = None, video_index: int = 0) -> list[str]:
    """One ffmpeg run: every moment as its own seeked input, faded, then joined.
    Progress goes to stdout (-progress pipe:1)."""
    use_nvenc = encoder.NVENC_AVAILABLE if nvenc is None else nvenc
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-nostdin", "-progress", "pipe:1", "-nostats"]
    for s in starts:
        cmd += ["-ss", str(s), "-t", str(length), "-i", src]
    n = len(starts)
    if not audio:
        cmd += ["-f", "lavfi", "-t", str(length * n), "-i", "anullsrc=r=48000:cl=stereo"]
    fo = max(0.0, length - FADE_S)
    parts, joins = [], []
    for i in range(n):
        parts.append(f"[{i}:v:{video_index}]scale=-2:'min(720,ih)',fps=30,setsar=1,format=yuv420p,"
                     f"fade=t=in:st=0:d={FADE_S},fade=t=out:st={fo}:d={FADE_S},"
                     f"trim=0:{length},setpts=PTS-STARTPTS[v{i}]")
        if audio:
            parts.append(f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo,"
                         f"afade=t=in:st=0:d={FADE_S},afade=t=out:st={fo}:d={FADE_S},"
                         f"apad,atrim=0:{length},asetpts=PTS-STARTPTS[a{i}]")
            joins.append(f"[v{i}][a{i}]")
        else:
            joins.append(f"[v{i}]")
    if audio:
        parts.append(f"{''.join(joins)}concat=n={n}:v=1:a=1[v][a]")
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        parts.append(f"{''.join(joins)}concat=n={n}:v=1:a=0[v]")
        maps = ["-map", "[v]", "-map", f"{n}:a:0", "-shortest"]
    video = (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"] if use_nvenc
             else ["-c:v", "libx264", "-preset", "superfast", "-crf", "23", "-threads", "0"])
    return cmd + ["-filter_complex", ";".join(parts), *maps, *video,
                  "-c:a", "aac", "-ac", "2", "-b:a", "128k", "-movflags", "+faststart", dest]


def key(file_id: int, path: str, starts: list[float]) -> str:
    st = os.stat(path)
    h = hashlib.sha1(json.dumps([st.st_size, int(st.st_mtime), starts]).encode()).hexdigest()[:16]
    return f"{file_id}-{h}"


def path_for(file_id: int, path: str, starts: list[float]) -> Path:
    return cache_dir() / f"{key(file_id, path, starts)}.mp4"


_teaser_slots = threading.BoundedSemaphore(1)     # one teaser at a time; never blocks previews
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _run(job: dict, path: str, starts: list[float], length: float, out: Path) -> None:
    tmp = out.with_suffix(".tmp.mp4")
    total_ms = max(1.0, length * len(starts) * 1_000_000)
    try:
        with _teaser_slots:
            job["state"] = "building"
            vi, audio = probe(path)
            cmd = teaser_cmd(path, starts, length, str(tmp), audio=audio, video_index=vi)
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    stdin=subprocess.DEVNULL, text=True)
            timer = threading.Timer(BUILD_TIMEOUT_S, proc.kill)
            timer.start()
            try:
                for line in proc.stdout:
                    if line.startswith("out_time_ms=") or line.startswith("out_time_us="):
                        try:
                            job["pct"] = min(99, int(int(line.split("=", 1)[1]) / total_ms * 100))
                        except ValueError:
                            pass
                err = proc.stderr.read()
                proc.wait()
            finally:
                timed_out = not timer.is_alive() and proc.returncode not in (0,)
                timer.cancel()
            if proc.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0:
                os.replace(tmp, out)
                job.update(state="done", pct=100)
                return
            last = next((ln for ln in reversed((err or "").strip().splitlines()) if ln.strip()), "")
            logger.warning("Teaser failed for %s: %s", path, (err or "")[-600:])
            if timed_out:
                job.update(state="error", error="Making the teaser took too long. Try fewer moments.")
            else:
                job.update(state="error", error=f"Couldn't make the teaser{': ' + last if last else ''}")
    except OSError as exc:
        job.update(state="error", error=f"Couldn't make the teaser: {exc.strerror or exc}")
    finally:
        tmp.unlink(missing_ok=True)


def start(file_id: int, path: str, starts: list[float], duration: float | None) -> str:
    """Start (or join) the build of this teaser in the background; returns its key."""
    out = path_for(file_id, path, starts)
    k = out.stem
    with _jobs_lock:
        job = _jobs.get(k)
        if out.is_file() and out.stat().st_size > 0:
            _jobs[k] = {"state": "done", "pct": 100, "error": None}
            return k
        if job and job["state"] in ("queued", "building"):
            return k
        job = _jobs[k] = {"state": "queued", "pct": 0, "error": None}
    out.parent.mkdir(parents=True, exist_ok=True)
    length = clip_len(float(duration or 0), len(starts))
    threading.Thread(target=_run, args=(job, path, starts, length, out), daemon=True,
                     name=f"teaser-{k}").start()
    return k


def status(k: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(k)
    if job is None:
        f = cache_dir() / f"{k}.mp4"
        return {"state": "done", "pct": 100, "error": None} if f.is_file() else None
    return dict(job)


def ready(file_id: int, path: str, starts: list[float]) -> Path | None:
    out = path_for(file_id, path, starts)
    return out if out.is_file() and out.stat().st_size > 0 else None


def build(file_id: int, path: str, starts: list[float], duration: float | None,
          wait: float = BUILD_TIMEOUT_S + 10) -> Path:
    """Build and wait (tests and scripts). Raises TeaserError."""
    import time
    k = start(file_id, path, starts, duration)
    end = time.monotonic() + wait
    while time.monotonic() < end:
        st = status(k) or {}
        if st.get("state") == "done":
            return path_for(file_id, path, starts)
        if st.get("state") == "error":
            raise TeaserError(st.get("error") or "Couldn't make the teaser")
        time.sleep(0.2)
    raise TeaserError("Making the teaser took too long")


def remove(file_id: int, path: str | None, starts: list[float]) -> None:
    try:
        if path and os.path.isfile(path):
            path_for(file_id, path, starts).unlink(missing_ok=True)
    except OSError:
        pass


def prune(days: int = 45) -> int:
    """Forget teasers nobody has played for a while (served ones are touched, and a
    missing one is rebuilt on demand)."""
    import time
    folder, n = cache_dir(), 0
    if not folder.is_dir():
        return 0
    cut = time.time() - days * 86400
    for f in folder.glob("*.mp4"):
        try:
            if f.stat().st_mtime < cut:
                f.unlink()
                n += 1
        except OSError:
            pass
    return n
