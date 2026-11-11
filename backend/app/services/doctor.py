"""Library doctor: find problems in the scanned folders and fix them.

Checks:
  sample      sample clips left in downloads ("sample" in the name, short or small)  safe
  junk        release clutter: .exe .url .lnk .txt .sfv .md5 .nzb …                    safe
  empty       folders with nothing left in them                                       safe
  broken      video files that can't be read (never indexed by a scan)
  missing     Library entries whose file is gone
  duplicate   the same episode or movie more than once; the best copy is kept
  audience    in the regular folder but rated for adults, or the other way round
              (only when Mature folders are set; uses the age rating or your choice)
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
    "missing": "File is gone", "duplicate": "Duplicate", "audience": "Wrong folder", "name": "Not named for Plex",
    "oversize": "Larger than target", "lowres": "Lower than preferred",
    "adult_in_plex": "Adult content in a Plex folder",
}
MAX_RATING_LOOKUPS = 200     # new TMDB lookups per check; the rest use the cache and catch up next week


def _audience_of(settings: dict, path: str) -> str | None:
    from . import organise
    return organise.audience_of(settings, path)


def _clearly_adult(db, settings: dict, path: str, where: dict, lookup: bool = True) -> str | None:
    """The rating, when a file in a regular folder is rated clearly for adults (R, NC-17,
    TV-MA…) and you haven't said otherwise. Where a file is counts as your decision; only
    an adult rating is worth asking about again (Keep here remembers the answer)."""
    from . import organise, ratings
    if not organise.mature_folders_set(settings) or not where.get("reason", "").startswith("already in the regular"):
        return None
    if where.get("kind") not in ("episode", "movie"):
        return None
    p = organise.parse(path)
    cert = ratings.certification(db, "tv" if p.kind == "episode" else "movie", where.get("show_hint") or p.title,
                                 p.year, lookup=lookup)
    return cert if (cert or "").strip().upper() in ratings.ADULT else None


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


def _in_any(path: str, roots) -> bool:
    return any(path == r or path.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def _music_roots(db: Session) -> list[str]:
    """Music folders belong to the music doctor (it keeps .txt setlists, .cue and .log)."""
    from . import music_library
    try:
        return [os.path.normpath(r) for r in music_library.roots(db)]
    except Exception:
        logger.exception("Couldn't list the music folders")
        return []


def _empty_trees(root: str, skip=()) -> list[str]:
    """The top-most folders under `root` that hold no files at any depth."""
    empty: set[str] = set()
    for here, dirs, files in os.walk(root, topdown=False):
        if here != root and not files and all(os.path.join(here, d) in empty for d in dirs):
            empty.add(here)
    return sorted(d for d in empty if os.path.dirname(d) not in empty and not _in_any(d, skip) and not
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
    music = _music_roots(db)
    rows = {r["path"]: r for r in store.get_all_files()}
    issues: list[dict] = []
    lookups = [0]

    for root in roots:
        if not os.path.isdir(root):
            continue
        for here, dirs, files in os.walk(root, topdown=False):
            if any(part.startswith(".") for part in os.path.relpath(here, root).split(os.sep) if part != "."):
                continue
            if _in_any(here, music):
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
        for folder in _empty_trees(root, music):
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

    # Names and folders: only when Rename & organise knows where files belong. A file
    # rated for adults in a regular folder (or the reverse) shows up as a wrong folder.
    settings = organise.get_settings(db)
    if settings.get("organise_tv_dir") or settings.get("organise_movie_dir"):
        dup_paths = {i["path"] for i in issues if i["kind"] == "duplicate"}
        for path, row in rows.items():
            if path in dup_paths or not os.path.exists(path):
                continue
            try:
                where = organise.plan(db, path, lookup=lookups[0] < MAX_RATING_LOOKUPS)
            except organise.OrganiseError:
                continue
            lookups[0] += 1
            dest = where["target"]
            adult = _clearly_adult(db, settings, path, where, lookup=lookups[0] <= MAX_RATING_LOOKUPS)
            if adult:
                to = organise.plan(db, path, override="mature", lookup=False)["target"]
                issues.append(_issue("audience", path, f"In a regular folder but rated {adult}: {to}",
                                     "Move to Mature", file_id=row["id"], target=to, audience="mature",
                                     keep_here=True))
                continue
            if os.path.abspath(dest) == os.path.abspath(path):
                continue
            issues.append(_issue("name", path, f"Would become {dest}", "Rename & organise",
                                 file_id=row["id"], target=dest, audience=where["audience"]))

    # Size against the folder's target: bigger (shrink only when asked) or below the preferred range.
    try:
        from .media import targets
        tfolders = targets.load_folders(db)
        seen = {i["path"] for i in issues if i["kind"] in ("duplicate", "missing")}
        for path, row in rows.items():
            if path in seen or row["status"] in ("queued", "processing") or not row["height"]:
                continue
            c = targets.check(row, tfolders)
            res = targets.LABEL.get(c.resolution, "?")
            if c.above and c.meets:
                to = targets.RES_KEY[c.target.max]
                issues.append(_issue("oversize", path,
                                     f"{res}; the target is {c.target.describe()}",
                                     f"Shrink to {targets.LABEL[c.target.max]}", file_id=row["id"], resolution=to))
            elif c.below:
                issues.append(_issue("lowres", path, f"{res}; {c.target.describe()} is preferred",
                                     "Find a better copy", file_id=row["id"], fixable=False))
    except Exception:
        logger.exception("Target-size check failed")

    # Adult content inside the Plex folders (moved there before folders could be protected).
    try:
        issues.extend(_adult_in_plex(db, rows))
    except Exception:
        logger.exception("Adult-content check failed")

    order = {k: i for i, k in enumerate(["adult_in_plex", *LABEL])}
    issues.sort(key=lambda i: (order[i["kind"]], i["path"]))
    return {"checked_at": _now(), "roots": roots, "issues": issues[:MAX_ISSUES],
            "total": len(issues), "fixed": []}


def _organise_dirs(settings: dict) -> list[tuple[str, str]]:
    return [(k, os.path.abspath(v.strip())) for k in ("organise_tv_dir", "organise_tv_mature_dir",
            "organise_movie_dir", "organise_movie_mature_dir") if (v := settings.get(k) or "").strip()]


def protected_folders(db: Session) -> list[tuple[str, str]]:
    """[(name, local root)] of protected folders that exist here."""
    from ..models import DownloadPath
    from . import folders
    out = []
    for dp in db.query(DownloadPath).filter(DownloadPath.protected == True).all():  # noqa: E712
        root = folders.local_of(db, dp)
        if root and os.path.isdir(root):
            out.append((dp.name, root))
    return out


def _adult_in_plex(db: Session, rows: dict) -> list[dict]:
    """Files in the TV / Movies (and Mature) folders that belong in a protected folder."""
    from ..models import ContentRating, OrganiseMove, PipelinePlan
    from . import adult, organise, ratings

    dirs = _organise_dirs(organise.get_settings(db))
    if not dirs:
        return []
    extra = adult.extra_keywords(db)
    protected = protected_folders(db)
    origin = {m.to_path: m.from_path for m in db.query(OrganiseMove).order_by(OrganiseMove.id)}
    for p in db.query(PipelinePlan).filter(PipelinePlan.organised_to.isnot(None)):
        origin.setdefault(p.organised_to, p.source_path)
    from .show_metadata import tmdb_key
    can_ask = bool(tmdb_key(db))
    asked = [0]

    def unknown_to_tmdb(title: str, year: int | None) -> bool:
        """True when TMDB has been asked and has no rating (no such film, or unrated)."""
        row = db.query(ContentRating).filter_by(kind="movie", title_key=ratings._key(title), year=year or 0).first()
        if row is None:
            if not can_ask or asked[0] >= MAX_RATING_LOOKUPS:
                return False
            asked[0] += 1
            return ratings.certification(db, "movie", title, year, lookup=True) is None and \
                db.query(ContentRating).filter_by(kind="movie", title_key=ratings._key(title),
                                                  year=year or 0).first() is not None
        return not row.cert
    out = []
    for path, row in rows.items():
        p = os.path.abspath(path)
        where = next((k for k, d in dirs if p.startswith(d.rstrip(os.sep) + os.sep)), None)
        if where is None or not os.path.exists(path):
            continue
        src = origin.get(path)
        why = None
        if src and organise.protected_root(src, db):
            why = f"Moved here from the protected folder {organise.protected_root(src, db)}"
        elif (hint := adult.looks_adult(path, extra)):
            why = f"Looks like adult content ({hint})"
        elif where == "organise_movie_mature_dir":
            parsed = organise.parse(path)
            if parsed.kind == "movie" and unknown_to_tmdb(parsed.title, parsed.year):
                why = "TMDB has no rating or match for this title, so it may not be a real movie (check it)"
        if not why:
            continue
        dest = src if (src and os.path.isdir(os.path.dirname(src)) and not os.path.exists(src)) else (
            os.path.join(protected[0][1], os.path.basename(path)) if protected else None)
        out.append(_issue("adult_in_plex", path, why + (f". Goes back to {dest}" if dest else
                                                          ". Mark a folder as Protected to move it there"),
                          f"Move back to {os.path.dirname(dest)}" if dest else "Protect a folder first",
                          file_id=row["id"], target=dest))
    return out


def move_back(db: Session, file_id: int, dest: str) -> str:
    """Move a Library file (and its subtitles) to dest, outside the Plex folders. Returns dest."""
    from . import organise
    from .media import store
    row = store.get_file(file_id)
    if row is None:
        raise ValueError("No longer in the Library")
    if os.path.exists(dest):
        raise ValueError(f"{dest} already exists")
    src = row["path"]
    organise.move(src, dest)
    store.rename_file(file_id, dest, os.path.basename(dest))
    organise._log_move(file_id, src, dest)
    return dest


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
    if kind in ("name", "audience"):
        if row is None:
            raise ValueError("No longer in the Library")
        where = organise.plan(db, path, override=issue.get("audience"))
        dest = organise.organise_file(where["settings"], row["id"], where["show_hint"], where["audience"],
                                      where.get("genre"))
        return f"Moved to {dest}"
    if kind == "adult_in_plex":
        if row is None:
            raise ValueError("No longer in the Library")
        dest = issue.get("target")
        if not dest:
            raise ValueError("Mark a folder as Protected (Settings → Folders) so there's somewhere to move it")
        return f"Moved back to {move_back(db, row['id'], dest)}"
    if kind == "oversize":
        if row is None:
            raise ValueError("No longer in the Library")
        res = issue.get("resolution")
        if res not in config_resolutions():
            raise ValueError("Unknown target size")
        store.create_job(row["id"], "encode", res, reason="Library doctor: shrink to the folder's target")
        store.set_file_status(row["id"], "queued")
        return f"Queued: shrink to {res}"
    if kind == "lowres":
        raise ValueError("Find a better copy with Search; there's nothing to fix in place")
    raise ValueError(f"Unknown problem type {kind}")


def config_resolutions() -> set[str]:
    from .media import config
    return set(config.RESOLUTION_MAP)


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
            music = _music_roots(db)
            for root in report["roots"]:
                for folder in (_empty_trees(root, music) if os.path.isdir(root) else []):
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
        if any(i["kind"] in ("audience", "name") for i in done):
            refresh_media_servers(db)                  # once, so Plex drops what moved
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


def refresh_media_servers(db) -> None:
    from .auto_organise import _refresh_media_servers
    _refresh_media_servers(db)


def forget_audience(db, title_key: str, kind: str) -> int:
    """Drop the saved "rated for adults" questions about one title (after Keep here or a move)."""
    from . import organise, ratings
    with _lock:
        report = last_report(db)
        if not report:
            return 0
        keep, gone = [], 0
        for i in report.get("issues", []):
            p = organise.parse(i.get("path", ""))
            if i.get("kind") == "audience" and p.kind == kind and ratings._key(p.title or "") == title_key:
                gone += 1
            else:
                keep.append(i)
        if gone:
            report["issues"], report["total"] = keep, max(0, report.get("total", 0) - gone)
            _set(db, REPORT_KEY, json.dumps(report))
        return gone
