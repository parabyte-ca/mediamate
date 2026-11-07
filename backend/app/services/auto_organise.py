"""Nightly: organise loose new files in the TV and movie libraries (organise_auto).

Takes over from the organize_tv.py / organize_movies.py scripts. Only "loose" files are
touched, never what's already in place:

* TV:     anything not already in <Show>/Season NN/ (or Specials)
* Movies: anything not already in a genre folder (genre folders on) or in a
          Title (Year) folder (off). Folders on the skip list are left alone.

Each file goes through Rename & organise (age-rating folders, genre folders, the
adult-content and protected-folder guards). A file whose name can't be read goes to
<library>/_Unsorted, never deleted. Files still downloading or busy are skipped.
The report (moved, borderline genre picks, unsorted, skipped) is saved, shown in
Settings, and sent as a notification when something needs a look.
"""
import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime, timezone

from . import movie_genres, organise

logger = logging.getLogger(__name__)

REPORT_KEY = "organise_auto_report"
MAX_LOOKUPS = 200            # new TMDB lookups per run; the rest wait for the next night
MIN_AGE_SEC = 3600           # leave files that changed in the last hour (still copying)
_SEASON = re.compile(r"(?i)^(season \d+|specials)$")
_lock = threading.Lock()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def libraries(settings: dict) -> list[tuple[str, str]]:
    """[(kind, root)] for the library folders that are set: tv / movie."""
    out = []
    for key, kind in (("organise_tv_dir", "tv"), ("organise_tv_mature_dir", "tv"),
                      ("organise_movie_dir", "movie"), ("organise_movie_mature_dir", "movie")):
        root = (settings.get(key) or "").strip()
        if root and os.path.isdir(root):
            out.append((kind, os.path.normpath(root)))
    # Deepest first, so a Mature folder inside a regular one wins.
    return sorted(out, key=lambda x: -len(x[1]))


def is_loose(kind: str, root: str, path: str, settings: dict, shelves=None) -> bool:
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    if rel.startswith("..") or any(p.startswith(".") for p in parts[:-1]):
        return False
    if parts[0] == movie_genres.UNSORTED:
        return False
    if kind == "tv":
        return not (len(parts) >= 3 and _SEASON.match(parts[-2]))
    if len(parts) >= 2 and parts[0].lower() in movie_genres.skip_list(settings):
        return False
    # On a shelf (a genre folder): placed, whether or not genre folders are switched on.
    shelves = movie_genres.genre_folders(root, settings) if shelves is None else shelves
    if len(parts) >= 2 and parts[0] in shelves:
        return False
    if movie_genres.enabled(settings):
        return True
    return not (len(parts) >= 2 and movie_genres._TITLE_YEAR.search(parts[0]))


def _downloading(db) -> list[str]:
    """Names of torrents still in qBittorrent: their files are left alone."""
    from ..models import Download
    rows = (db.query(Download).filter(Download.qbit_removed.is_(False),
                                      Download.status.notin_(["failed", "error"])).all())
    return [n for n in (_norm(d.title) for d in rows) if len(n) >= 6]


def _unsort(root: str, row: dict) -> str:
    from .media import store
    dest_dir = os.path.join(root, movie_genres.UNSORTED)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(row["path"]))
    if os.path.exists(dest):
        raise organise.OrganiseError(f"{dest} already exists")
    shutil.move(row["path"], dest)
    store.rename_file(row["id"], dest, os.path.basename(dest))
    organise._prune_empty(os.path.dirname(row["path"]))
    return dest


NIGHTLY_LIMIT = 50           # moves per night; the rest wait (a big first run shouldn't happen unseen)


def run(db, dry_run: bool = False, limit: int | None = NIGHTLY_LIMIT) -> dict:
    """Organise the loose files now. Returns (and saves) the report."""
    from .media import store
    with _lock:
        settings = organise.get_settings(db)
        libs = libraries(settings)
        report = {"ran_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "dry_run": dry_run,
                  "moved": [], "borderline": [], "unsorted": [], "skipped": [], "errors": []}
        if not libs:
            report["errors"].append("No TV or Movies library folder is set (Settings → Folders → Library for)")
            return _save(db, report)
        busy = _downloading(db)
        now = datetime.now().timestamp()
        lookups = 0
        shelves: dict = {}
        for row in store.get_all_files():
            path = row["path"]
            lib = next(((k, r) for k, r in libs if path.startswith(r.rstrip(os.sep) + os.sep)), None)
            if lib is None or not os.path.isfile(path):
                continue
            kind, root = lib
            if kind == "movie" and root not in shelves:
                shelves[root] = movie_genres.genre_folders(root, settings)
            if not is_loose(kind, root, path, settings, shelves.get(root)):
                continue
            name = os.path.relpath(path, root)
            if movie_genres.is_extra(root, path):
                report["skipped"].append(f"{name}: an extra (trailer, interview…), left where it is")
                continue
            if not dry_run and limit and len(report["moved"]) + len(report["unsorted"]) >= limit:
                report["skipped"].append(f"{name}: over this run's limit of {limit}; next time")
                continue
            if row["status"] in ("queued", "processing") or store.get_active_job_for_file(row["id"]) is not None:
                report["skipped"].append(f"{name}: being processed")
                continue
            try:
                if now - os.path.getmtime(path) < MIN_AGE_SEC:
                    report["skipped"].append(f"{name}: changed in the last hour")
                    continue
            except OSError:
                continue
            parts = {_norm(p) for p in name.split(os.sep)} | {_norm(os.path.splitext(os.path.basename(path))[0])}
            if any(b in parts for b in busy):
                report["skipped"].append(f"{name}: still in qBittorrent")
                continue
            parsed = organise.parse(path)
            try:
                if parsed.kind is None or (kind == "tv") != (parsed.kind == "episode"):
                    raise organise.OrganiseError("unreadable")
                where = organise.plan(db, path, lookup=lookups < MAX_LOOKUPS)
                lookups += 1
                db.commit()
                if os.path.normpath(where["target"]) == os.path.normpath(path):
                    continue
                entry = {"from": name, "to": os.path.relpath(where["target"], root)}
                if where.get("genre_confidence") == "borderline":
                    report["borderline"].append(entry)
                bucket = "unsorted" if where.get("genre_confidence") == "none" else "moved"
                if not dry_run:
                    organise.organise_file(where["settings"], row["id"], where["show_hint"], where["audience"],
                                           where.get("genre"))
                report[bucket].append(entry)
            except organise.OrganiseError as exc:
                msg = str(exc)
                if msg == "unreadable" or msg.startswith("Couldn't tell"):
                    try:
                        dest = name if dry_run else os.path.relpath(_unsort(root, row), root)
                        report["unsorted"].append({"from": name, "to": dest})
                    except (OSError, organise.OrganiseError) as e:
                        report["errors"].append(f"{name}: {e}")
                else:
                    report["skipped"].append(f"{name}: {msg}")
            except OSError as exc:
                report["errors"].append(f"{name}: {exc}")
        for key in ("moved", "borderline", "unsorted", "skipped", "errors"):
            report[f"{key}_count"] = len(report[key])
            report[key] = report[key][:200]
        _save(db, report)
    if report["moved"] and not dry_run:
        _refresh_media_servers(db)
    if not dry_run:
        _notify(db, report)
    return report


def _save(db, report: dict) -> dict:
    from ..models import Setting
    db.merge(Setting(key=REPORT_KEY, value=json.dumps(report)))
    db.commit()
    return report


def last_report(db) -> dict | None:
    from ..models import Setting
    row = db.get(Setting, REPORT_KEY)
    try:
        return json.loads(row.value) if row and row.value else None
    except ValueError:
        return None


def _refresh_media_servers(db) -> None:
    import asyncio
    from ..models import Setting
    from .media_servers import trigger_media_refresh
    try:
        asyncio.run(trigger_media_refresh({s.key: s.value for s in db.query(Setting).all()}))
    except Exception:
        logger.exception("Media server refresh after organising failed")


def _notify(db, report: dict) -> None:
    """Only when something needs a look: borderline genre picks, unsorted files or errors."""
    from ..models import Setting
    from .apprise_notify import notify
    n_b, n_u, n_e = report.get("borderline_count", 0), report.get("unsorted_count", 0), report.get("errors_count", 0)
    if not (n_b or n_u or n_e):
        return
    url = (db.get(Setting, "apprise_url").value if db.get(Setting, "apprise_url") else "") or ""
    parts = [f"{report.get('moved_count', 0)} organised"]
    if n_b:
        parts.append(f"{n_b} in a best-guess genre folder")
    if n_u:
        parts.append(f"{n_u} moved to _Unsorted")
    if n_e:
        parts.append(f"{n_e} errors")
    try:
        notify(url, title="MediaMate — Nightly organise", body=", ".join(parts) + ". Details: Settings → Rename & organise.",
               url="/settings#organise")
    except Exception:
        logger.exception("Organise notification failed")


def scheduled_run() -> None:
    from ..database import SessionLocal
    try:
        with SessionLocal() as db:
            if not organise.get_settings(db).get("organise_auto", "false").lower() == "true":
                return
            r = run(db)
        logger.info("Nightly organise: %d moved, %d borderline, %d unsorted, %d skipped, %d errors",
                    r["moved_count"], r["borderline_count"], r["unsorted_count"], r["skipped_count"],
                    r["errors_count"])
    except Exception:
        logger.exception("Nightly organise failed")
