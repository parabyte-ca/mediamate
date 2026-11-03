"""Library doctor: find problems in the scanned folders and fix them.

Checks:
  sample      sample clips left in downloads ("sample" in the name, short or small)  safe
  junk        release clutter: .exe .url .lnk .txt .sfv .md5 .nzb …                    safe
  empty       folders with nothing left in them                                       safe
  broken      video files that can't be read (never indexed by a scan)
  missing     Library entries whose file is gone
  duplicate   the same episode or movie more than once; the best copy is kept
  name        files that aren't named the Plex way (only when Rename & organise
              has folders set; fixing moves them there)

"Safe" problems are fixed automatically after each check (setting doctor_auto_fix,
on by default); the rest wait for a Fix button. Files always go to the 30-day
.trash, never straight to deletion. Busy files (being encoded or split) are skipped.
"""
import hashlib
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Setting

logger = logging.getLogger(__name__)

REPORT_KEY = "doctor_report"
AUTO_KEY = "doctor_auto_fix"
SAFE = ("sample", "junk", "empty")
JUNK_EXTS = {".exe", ".url", ".lnk", ".txt", ".sfv", ".md5", ".nzb", ".bat", ".scr", ".com", ".par2", ".website"}
SAMPLE_MAX_BYTES = 300 * 1024 * 1024
SAMPLE_MAX_SECONDS = 5 * 60
MAX_ISSUES = 1000

LABEL = {
    "sample": "Sample clip", "junk": "Leftover file", "empty": "Empty folder", "broken": "Can't be played",
    "missing": "File is gone", "duplicate": "Duplicate", "name": "Not named for Plex",
}
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _id(kind: str, path: str) -> str:
    return hashlib.sha1(f"{kind}:{path}".encode()).hexdigest()[:12]


def _issue(kind: str, path: str, detail: str, fix: str, **extra) -> dict:
    return {"id": _id(kind, path), "kind": kind, "label": LABEL[kind], "path": path,
            "detail": detail, "fix": fix, "safe": kind in SAFE, **extra}


def _get(db: Session, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row and row.value is not None else default


def _set(db: Session, key: str, value: str) -> None:
    db.merge(Setting(key=key, value=value))
    db.commit()


def auto_fix_enabled(db: Session) -> bool:
    return _get(db, AUTO_KEY, "true").lower() == "true"


_SAMPLE_WORD = re.compile(r"(?<![a-z0-9])sample(?![a-z0-9])")


def _is_sample(name: str, size: int, duration: float | None) -> bool:
    tagged = bool(_SAMPLE_WORD.search(os.path.splitext(name)[0].lower()))   # "sample", not "Samples Of Life"
    return tagged and (size < SAMPLE_MAX_BYTES or (duration is not None and duration < SAMPLE_MAX_SECONDS))


def _quality(row) -> tuple:
    """Which copy of a duplicate to keep: higher resolution, then HEVC, then smaller."""
    return ((row["height"] or 0), 1 if (row["codec"] or "").lower() == "hevc" else 0, -(row["size_bytes"] or 0))


def _empty_trees(root: str) -> list[str]:
    """The top-most folders under `root` that hold no files at any depth."""
    empty: set[str] = set()
    for here, dirs, files in os.walk(root, topdown=False):
        if here != root and not files and all(os.path.join(here, d) in empty for d in dirs):
            empty.add(here)
    return sorted(d for d in empty if os.path.dirname(d) not in empty and not
                  any(part.startswith(".") for part in os.path.relpath(d, root).split(os.sep)))


def _remove_tree(path: str) -> None:
    """Remove an empty folder tree; stops (OSError) if a file turned up in it."""
    for here, dirs, files in os.walk(path, topdown=False):
        if files:
            raise OSError(f"{here} isn't empty any more")
        os.rmdir(here)


def check(db: Session) -> dict:
    """Look through every scanned folder and the Library. Read-only."""
    from . import folders, organise
    from .media import codec_detector, config, store

    roots = folders.scan_roots(db)
    rows = {r["path"]: r for r in store.get_all_files()}
    issues: list[dict] = []

    for root in roots:
        if not os.path.isdir(root):
            continue
        for here, dirs, files in os.walk(root, topdown=False):
            if any(part.startswith(".") for part in os.path.relpath(here, root).split(os.sep) if part != "."):
                continue
            for name in files:
                if name.startswith("."):
                    continue
                path = os.path.join(here, name)
                ext = os.path.splitext(name)[1].lower()
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                if ext in config.VIDEO_EXTENSIONS:
                    row = rows.get(path)
                    if _is_sample(name, size, row["duration_sec"] if row else None):
                        issues.append(_issue("sample", path, f"{size / 1e6:.0f} MB sample clip", "Move to trash"))
                    elif row is None:
                        info, err = codec_detector.probe_with_error(path)
                        if info is None:
                            issues.append(_issue("broken", path, f"Can't be read: {err or 'unknown error'}"[:200],
                                                 "Move to trash"))
                elif ext in JUNK_EXTS:
                    issues.append(_issue("junk", path, f"{ext} file left over from a download", "Move to trash"))
        for folder in _empty_trees(root):
            issues.append(_issue("empty", folder, "Nothing in this folder", "Remove the folder"))

    # Library entries whose file has gone.
    for path, row in rows.items():
        if not os.path.exists(path):
            issues.append(_issue("missing", path, "In the Library, but the file isn't there any more",
                                 "Remove from the Library", file_id=row["id"]))

    # Duplicates: the same episode or movie more than once.
    groups: dict[tuple, list] = {}
    for path, row in rows.items():
        if not os.path.exists(path) or _is_sample(row["filename"], row["size_bytes"] or 0, row["duration_sec"]):
            continue
        p = organise.parse(path)
        if p.kind == "episode":
            key = ("ep", p.title.lower(), p.season, p.episode)
        elif p.kind == "movie":
            key = ("mv", p.title.lower(), p.year)
        else:
            continue
        groups.setdefault(key, []).append(row)
    for group in groups.values():
        if len(group) < 2:
            continue
        best = max(group, key=_quality)
        for row in group:
            if row is best:
                continue
            res = f"{row['height']}p" if row["height"] else "?"
            issues.append(_issue("duplicate", row["path"],
                                 f"Another copy is kept: {best['filename']} ({best['height'] or '?'}p, "
                                 f"{(best['codec'] or '?').upper()}). This one: {res}, {(row['codec'] or '?').upper()}",
                                 "Move this copy to trash", file_id=row["id"], keep=best["path"]))

    # Names: only when Rename & organise knows where files belong.
    settings = organise.get_settings(db)
    if settings.get("organise_tv_dir") or settings.get("organise_movie_dir"):
        dup_paths = {i["path"] for i in issues if i["kind"] == "duplicate"}
        for path, row in rows.items():
            if path in dup_paths or not os.path.exists(path):
                continue
            try:
                dest, _ = organise.target_for(settings, path)
            except organise.OrganiseError:
                continue
            if os.path.abspath(dest) != os.path.abspath(path):
                issues.append(_issue("name", path, f"Would become {dest}", "Rename & organise",
                                     file_id=row["id"], target=dest))

    order = {k: i for i, k in enumerate(LABEL)}
    issues.sort(key=lambda i: (order[i["kind"]], i["path"]))
    return {"checked_at": _now(), "roots": roots, "issues": issues[:MAX_ISSUES],
            "total": len(issues), "fixed": []}


def fix(db: Session, issue: dict) -> str:
    """Fix one issue. Returns what was done; raises OSError/ValueError with a reason."""
    from . import organise
    from .media import store, trash

    kind, path = issue["kind"], issue["path"]
    row = store.get_file_by_path(path)
    if row is not None and (store.get_active_job_for_file(row["id"]) is not None
                            or row["status"] in ("queued", "processing")):
        raise ValueError("It's being processed right now; try again later")
    if kind in ("sample", "junk", "broken", "duplicate"):
        if os.path.exists(path):
            trash.move_to_trash(path)
        if row is not None:
            store.delete_file(row["id"])
        return "Moved to trash"
    if kind == "empty":
        _remove_tree(path)  # fails safely if something appeared in it since
        return "Removed the empty folder"
    if kind == "missing":
        if row is not None:
            store.delete_file(row["id"])
        return "Removed from the Library"
    if kind == "name":
        if row is None:
            raise ValueError("No longer in the Library")
        dest = organise.organise_file(organise.get_settings(db), row["id"])
        return f"Moved to {dest}"
    raise ValueError(f"Unknown problem type {kind}")


def run(db: Session, auto_fix: bool | None = None) -> dict:
    """Check, fix the safe problems (if enabled), and save the report."""
    with _lock:
        report = check(db)
        if auto_fix if auto_fix is not None else auto_fix_enabled(db):
            # Files before folders, so a folder emptied by the fixes goes too.
            safe = [i for i in report["issues"] if i["safe"] and i["kind"] != "empty"]
            for issue in safe:
                try:
                    issue["result"] = fix(db, issue)
                    report["fixed"].append(issue)
                except (OSError, ValueError) as exc:
                    issue["error"] = str(exc)
            # Folders last, looked for again: the fixes above can empty them.
            report["issues"] = [i for i in report["issues"] if i["kind"] != "empty"]
            for root in report["roots"]:
                for folder in (_empty_trees(root) if os.path.isdir(root) else []):
                    issue = _issue("empty", folder, "Nothing in this folder", "Remove the folder")
                    try:
                        issue["result"] = fix(db, issue)
                        report["fixed"].append(issue)
                    except (OSError, ValueError) as exc:
                        issue["error"] = str(exc)
                        report["issues"].append(issue)
            fixed_ids = {i["id"] for i in report["fixed"]}
            report["issues"] = [i for i in report["issues"] if i["id"] not in fixed_ids]
            report["total"] = len(report["issues"])
        _set(db, REPORT_KEY, json.dumps(report))
        return report


def last_report(db: Session) -> dict | None:
    raw = _get(db, REPORT_KEY)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def fix_ids(db: Session, ids: list[str]) -> dict:
    """Fix chosen problems from the last report; update the saved report."""
    with _lock:
        report = last_report(db) or {"issues": [], "fixed": [], "total": 0}
        wanted = set(ids)
        done, errors = [], []
        for issue in [i for i in report["issues"] if i["id"] in wanted]:
            try:
                issue["result"] = fix(db, issue)
                done.append(issue)
            except (OSError, ValueError) as exc:
                errors.append(f"{os.path.basename(issue['path'])}: {exc}")
        done_ids = {i["id"] for i in done}
        report["issues"] = [i for i in report["issues"] if i["id"] not in done_ids]
        report["total"] = max(0, report.get("total", 0) - len(done_ids))
        report["fixed"] = (report.get("fixed") or []) + done
        _set(db, REPORT_KEY, json.dumps(report))
        return {"done": len(done), "errors": errors, "report": report}


def scheduled_run() -> None:
    """Weekly job."""
    from ..database import SessionLocal
    try:
        with SessionLocal() as db:
            r = run(db)
        logger.info("Library doctor: %d problem(s) left, %d fixed automatically", r["total"], len(r["fixed"]))
    except Exception:
        logger.exception("Library doctor failed")
