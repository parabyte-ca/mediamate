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
from pathlib import Path

from . import encoder, preview

logger = logging.getLogger(__name__)

MOMENTS = 6
CLIP_S = 5.0                # each moment
MAX_TOTAL_S = 30.0
FADE_S = 0.25
BUILD_TIMEOUT_S = 180


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


def has_audio(path: str) -> bool:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                            "stream=index", "-of", "csv=p=0", path],
                           capture_output=True, text=True, timeout=30, check=False)
        return bool(r.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def teaser_cmd(src: str, starts: list[float], length: float, dest: str,
               audio: bool = True, nvenc: bool | None = None) -> list[str]:
    """One ffmpeg run: every moment as its own seeked input, faded, then joined."""
    use_nvenc = encoder.NVENC_AVAILABLE if nvenc is None else nvenc
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-nostdin"]
    for s in starts:
        cmd += ["-ss", str(s), "-t", str(length), "-i", src]
    n = len(starts)
    if not audio:
        cmd += ["-f", "lavfi", "-t", str(length * n), "-i", "anullsrc=r=48000:cl=stereo"]
    fo = max(0.0, length - FADE_S)
    parts, joins = [], []
    for i in range(n):
        parts.append(f"[{i}:v:0]scale=-2:'min(720,ih)',fps=30,setsar=1,format=yuv420p,"
                     f"fade=t=in:st=0:d={FADE_S},fade=t=out:st={fo}:d={FADE_S},"
                     f"setpts=PTS-STARTPTS[v{i}]")
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
             else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"])
    return cmd + ["-filter_complex", ";".join(parts), *maps, *video,
                  "-c:a", "aac", "-ac", "2", "-b:a", "128k", "-movflags", "+faststart", dest]


def key(file_id: int, path: str, starts: list[float]) -> str:
    st = os.stat(path)
    h = hashlib.sha1(json.dumps([st.st_size, int(st.st_mtime), starts]).encode()).hexdigest()[:16]
    return f"{file_id}-{h}"


def path_for(file_id: int, path: str, starts: list[float]) -> Path:
    return cache_dir() / f"{key(file_id, path, starts)}.mp4"


def build(file_id: int, path: str, starts: list[float], duration: float | None) -> Path:
    """The teaser for these moments, making it if needed. Raises TeaserError."""
    out = path_for(file_id, path, starts)
    if out.is_file() and out.stat().st_size > 0:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    length = clip_len(float(duration or 0), len(starts))
    tmp = out.with_suffix(".tmp.mp4")
    with preview._player_slots:            # shares the 2-at-a-time limit with conversions
        if out.is_file() and out.stat().st_size > 0:
            return out
        try:
            r = subprocess.run(teaser_cmd(path, starts, length, str(tmp), audio=has_audio(path)),
                               capture_output=True, text=True, timeout=BUILD_TIMEOUT_S, check=False)
            if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
                logger.info("Teaser failed for %s: %s", path, (r.stderr or "")[-400:])
                raise TeaserError("Couldn't make the teaser from this video")
            os.replace(tmp, out)
        except subprocess.TimeoutExpired:
            raise TeaserError("Making the teaser took too long")
        except OSError as exc:
            raise TeaserError(f"Couldn't make the teaser: {exc.strerror or exc}")
        finally:
            tmp.unlink(missing_ok=True)
    return out


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
