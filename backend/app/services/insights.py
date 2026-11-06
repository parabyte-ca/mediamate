"""Insights: what MediaMate has done for the library, and what's left to gain.

Everything is computed from tables that already exist (media_jobs, media_files,
download_history, settings) — there is no separate ledger to drift.
Days are the server's local days (TZ), so "today" matches the user's clock.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

CODEC_NAMES = {
    "hevc": "HEVC (x265)", "h264": "H.264", "av1": "AV1", "vp9": "VP9",
    "mpeg4": "MPEG-4", "mpeg2video": "MPEG-2", "vc1": "VC-1", "wmv3": "WMV",
}
TOP_CODECS = 5  # the rest fold into "Other"
RES_BUCKETS = [(2000, "4K"), (1300, "1440p"), (1000, "1080p"), (700, "720p"), (470, "480p")]


def _local_day(value) -> date | None:
    """A stored timestamp (ISO string or naive-UTC datetime) → the server's local date."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().date()


def _seconds_between(a, b) -> float | None:
    try:
        start = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
    except ValueError:
        return None
    secs = (end - start).total_seconds()
    return secs if secs > 0 else None


def _res_label(height) -> str:
    for floor, label in RES_BUCKETS:
        if (height or 0) >= floor:
            return label
    return "Lower / unknown"


def compute(db: Session, days: int = 30) -> dict:
    from .pipeline import _encode_ratio

    days = max(1, min(int(days), 365))
    today = datetime.now().astimezone().date()
    first_day = today - timedelta(days=days - 1)
    since_utc = datetime.combine(first_day, datetime.min.time()).astimezone().astimezone(timezone.utc)

    # ── Savings per day (finished encodes) ──────────────────────────────────
    saved_by_day: dict[date, list[int]] = defaultdict(lambda: [0, 0])
    jobs = db.execute(text(
        "SELECT j.saved_bytes, j.finished_at, j.started_at, j.encoder, j.media_seconds, f.filename,"
        " f.size_bytes FROM media_jobs j LEFT JOIN media_files f ON f.id = j.file_id"
        " WHERE j.job_type='encode' AND j.status='done'")).all()
    for j in jobs:
        d = _local_day(j.finished_at)
        if d is not None and d >= first_day:
            saved_by_day[d][0] += j.saved_bytes or 0
            saved_by_day[d][1] += 1
    savings = [{"date": (first_day + timedelta(n)).isoformat(),
                "saved_bytes": saved_by_day[first_day + timedelta(n)][0],
                "encodes": saved_by_day[first_day + timedelta(n)][1]} for n in range(days)]

    # ── Downloads per day ───────────────────────────────────────────────────
    dl_by_day: dict[date, list[int]] = defaultdict(lambda: [0, 0])
    for added_at, size in db.execute(text(
            "SELECT added_at, size_bytes FROM download_history WHERE added_at >= :s"),
            {"s": since_utc.strftime("%Y-%m-%d %H:%M:%S")}):  # SQLAlchemy's stored DateTime format
        d = _local_day(added_at)
        if d is not None and d >= first_day:
            dl_by_day[d][0] += 1
            dl_by_day[d][1] += size or 0
    downloads = [{"date": (first_day + timedelta(n)).isoformat(),
                  "count": dl_by_day[first_day + timedelta(n)][0],
                  "bytes": dl_by_day[first_day + timedelta(n)][1]} for n in range(days)]

    # ── Library make-up ─────────────────────────────────────────────────────
    files = db.execute(text(
        "SELECT codec, height, size_bytes, duration_sec, status FROM media_files")).all()
    by_codec: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_res: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for f in files:
        key = (f.codec or "unknown").lower()
        by_codec[key][0] += 1
        by_codec[key][1] += f.size_bytes or 0
        label = _res_label(f.height)
        by_res[label][0] += 1
        by_res[label][1] += f.size_bytes or 0
    ranked = sorted(by_codec.items(), key=lambda kv: kv[1][1], reverse=True)
    codec_mix = [{"codec": k, "label": CODEC_NAMES.get(k, k.upper() if k != "unknown" else "Unknown"),
                  "files": v[0], "bytes": v[1]} for k, v in ranked[:TOP_CODECS]]
    rest = ranked[TOP_CODECS:]
    if rest:
        codec_mix.append({"codec": "other", "label": "Other", "files": sum(v[0] for _, v in rest),
                          "bytes": sum(v[1] for _, v in rest)})
    order = [label for _, label in RES_BUCKETS] + ["Lower / unknown"]
    resolution_mix = [{"label": k, "files": by_res[k][0], "bytes": by_res[k][1]} for k in order if by_res[k][0]]

    library_bytes = sum(v[1] for v in by_codec.values())
    hevc_bytes = by_codec.get("hevc", [0, 0])[1]

    # ── Encoding speed: seconds of video per second of work ─────────────────
    speed_acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0])
    for j in jobs:
        if not j.encoder or not j.media_seconds:
            continue
        wall = _seconds_between(j.started_at, j.finished_at)
        if not wall:
            continue
        acc = speed_acc[j.encoder]
        acc[0] += j.media_seconds
        acc[1] += wall
        acc[2] += 1
    speed = [{"encoder": enc, "label": "GPU (NVENC)" if enc == "nvenc" else "CPU (x265)",
              "jobs": int(a[2]), "realtime_x": round(a[0] / a[1], 2)}
             for enc, a in sorted(speed_acc.items()) if a[1] > 0]

    # ── What's left: files not yet in HEVC ──────────────────────────────────
    ratio = _encode_ratio(db)
    todo = [f for f in files if (f.codec or "").lower() != "hevc" and f.status not in ("done", "skipped")]
    todo_bytes = sum(f.size_bytes or 0 for f in todo)
    todo_seconds = sum(f.duration_sec or 0 for f in todo)
    from .media import config as media_config
    current = "nvenc" if media_config.ENCODER_BACKEND == "nvenc" else "cpu"
    pace = next((s["realtime_x"] for s in speed if s["encoder"] == current), None)
    forecast = {
        "files": len(todo), "bytes": todo_bytes, "est_saved_bytes": int(todo_bytes * ratio),
        "ratio": round(ratio, 3), "encoder": current,
        "est_hours": round(todo_seconds / pace / 3600, 1) if pace else None,
    }

    # ── Headlines & biggest wins ────────────────────────────────────────────
    total_row = db.execute(text("SELECT value FROM settings WHERE key='total_saved_bytes'")).first()
    top = sorted((j for j in jobs if (j.saved_bytes or 0) > 0), key=lambda j: j.saved_bytes, reverse=True)[:5]
    return {
        "days": days,
        "totals": {
            # The counter can be reset in Settings; never show less than the jobs on record.
            "saved_bytes": max(int(total_row[0]) if total_row and str(total_row[0]).isdigit() else 0,
                               sum(j.saved_bytes or 0 for j in jobs)),
            "saved_bytes_period": sum(d["saved_bytes"] for d in savings),
            "encodes_period": sum(d["encodes"] for d in savings),
            "downloads_period": sum(d["count"] for d in downloads),
            "downloaded_bytes_period": sum(d["bytes"] for d in downloads),
            "library_files": len(files),
            "library_bytes": library_bytes,
            "hevc_share": round(hevc_bytes / library_bytes, 3) if library_bytes else 0,
        },
        "savings_by_day": savings,
        "downloads_by_day": downloads,
        "codec_mix": codec_mix,
        "resolution_mix": resolution_mix,
        "speed": speed,
        "forecast": forecast,
        "top_savings": [{"filename": j.filename, "saved_bytes": j.saved_bytes,
                         "after_bytes": j.size_bytes, "finished_at": j.finished_at} for j in top],
    }
