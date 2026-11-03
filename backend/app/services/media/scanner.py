import os
import re
import logging

from . import config
from . import store as db
from . import codec_detector
from . import targets

logger = logging.getLogger(__name__)

_EPISODE_RE = re.compile(r's\d+e\d+', re.IGNORECASE)


def _is_anthology(filename: str, size_bytes: int, duration_sec: float) -> bool:
    if _EPISODE_RE.search(filename):
        return False  # TV episode naming (S01E02) — never an anthology
    long_enough = bool(duration_sec and duration_sec >= config.SPLIT_MIN_DURATION)
    big_enough  = bool(size_bytes  and size_bytes  >= config.SPLIT_MIN_SIZE)
    if not (long_enough or big_enough):
        return False
    lower = filename.lower()
    return any(k.lower() in lower for k in config.SPLIT_KEYWORDS)


def scan_all() -> dict:
    """Scan all configured MEDIA_PATHS and upsert file records. Returns summary."""
    found = skipped = errors = 0
    new_ids: list[int] = []
    try:
        folders = targets.load_folders()
    except Exception as exc:          # targets are a nicety: never stop a scan for them
        logger.warning("Couldn't load folder targets: %s", exc)
        folders = []

    for media_path in config.MEDIA_PATHS:
        if not os.path.isdir(media_path):
            logger.warning("Media path not found: %s", media_path)
            continue

        for root, dirs, files in os.walk(
            media_path,
            onerror=lambda e: logger.warning("Walk error: %s", e),
        ):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith(".")]

            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in config.VIDEO_EXTENSIONS:
                    continue

                full_path = os.path.join(root, fname)

                try:
                    # Skip probing files already finished — avoids re-scanning
                    # large completed libraries on every scan.
                    existing = db.get_file_by_path(full_path)
                    if existing and existing["status"] in ("done", "skipped", "ok"):
                        skipped += 1
                        continue

                    info, probe_err = codec_detector.probe_with_error(full_path)
                    if info is None:
                        logger.warning("Could not read %s: %s", full_path, probe_err)
                        errors += 1
                        continue

                    # Respect manual override — don't recalculate if user has overridden
                    if existing and existing["anthology_override"] is not None:
                        anthology = bool(existing["anthology_override"])
                    else:
                        anthology = _is_anthology(fname, info.size_bytes, info.duration_sec)
                    file_id = db.upsert_file(
                        path=full_path,
                        filename=fname,
                        size_bytes=info.size_bytes,
                        duration_sec=info.duration_sec,
                        codec=info.codec,
                        is_anthology=anthology,
                        width=info.width,
                        height=info.height,
                    )
                    found += 1
                    # Already H.265 (its folder's target)? Then it's "In target format", not pending.
                    try:
                        targets.classify(file_id, folders)
                    except Exception as exc:
                        logger.warning("Target check failed for %s: %s", full_path, exc)
                    if existing is None:
                        new_ids.append(file_id)
                    logger.info(
                        "Found: %s | codec=%s | %.0fs | anthology=%s",
                        fname, info.codec, info.duration_sec, anthology,
                    )

                except Exception as exc:
                    logger.error("Error scanning %s: %s", full_path, exc)
                    errors += 1

    return {"found": found, "skipped": skipped, "errors": errors, "new_ids": new_ids}
