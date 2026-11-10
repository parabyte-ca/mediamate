"""Music doctor: find problems in the music library and fix them.

Checks (album-level unless noted):
  junk           download clutter (.url .exe .sfv .par2 Thumbs.db …); .cue .log .nfo .txt
                 and playlists are kept, they belong to rips and bootlegs           safe
  empty          folders with nothing left in them                                  safe
  missing        albums in the Library whose folder is gone                        safe
  broken         audio files that can't be read (a file)
  untagged       tracks without title, artist, album or number → Fix tags (MusicBrainz)
  no_cover       no cover image in the folder → Find artwork
  not_embedded   the folder has a cover but the files don't → Put the cover in the files
  placement      not in Music library/Artist/Album (Year)/NN - Title → Organise for Plex
  duplicate      the same album more than once; the best copy is kept (lossless, then
                 bitrate, then most tracks)
  missing_tracks / low_bitrate
                 → mark the album Wanted on the artist's Watchlist card, so a better
                   copy downloads (only for artists you follow)

"Safe" problems are fixed automatically after each check when the Library doctor's
"fix automatically" switch is on. Tags, artwork and covers in files call MusicBrainz
(one request a second), so they run in the background as a bulk action. Files go to
the music folder's 30-day .trash, never straight to deletion.
"""
import json
import logging
import os
import shutil
import threading
from datetime import date, datetime, timedelta

from . import doctor, music_library
from .doctor import _id, _now

logger = logging.getLogger(__name__)

REPORT_KEY = "music_doctor_report"
SAFE = ("junk", "empty", "missing")
BATCH = {"untagged": "fix_tags", "no_cover": "covers", "not_embedded": "embed",
         "low_bitrate": "upgrade", "missing_tracks": "complete"}
JUNK_EXTS = {".exe", ".url", ".lnk", ".sfv", ".md5", ".nzb", ".bat", ".scr", ".com", ".par2", ".website",
             ".torrent"}
JUNK_NAMES = {"thumbs.db", "desktop.ini"}
MAX_ISSUES = 2000
TRASH = ".trash"

LABEL = {
    "broken": "Can't be read", "duplicate": "Duplicate album", "placement": "Not organised for Plex",
    "untagged": "Missing tags", "no_cover": "No artwork", "not_embedded": "Cover not in the files",
    "missing_tracks": "Missing tracks", "low_bitrate": "Low bitrate", "missing": "Folder is gone",
    "junk": "Leftover file", "empty": "Empty folder",
}
_lock = threading.Lock()


def _issue(kind: str, path: str, detail: str, fix: str, **extra) -> dict:
    return {"id": _id("music-" + kind, path), "kind": kind, "label": LABEL[kind], "path": path,
            "detail": detail, "fix": fix, "safe": kind in SAFE, **extra}


def _under(path: str, root: str) -> bool:
    path, root = os.path.normpath(path), os.path.normpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _root_of(path: str, roots: list[str]) -> str | None:
    return max((r for r in roots if _under(path, r)), key=len, default=None)


def trash(path: str, roots: list[str]) -> str:
    """Move ``path`` (file or folder) to <music root>/.trash/<date>/<relative path>."""
    root = _root_of(path, roots)
    if root is None or os.path.normpath(path) == os.path.normpath(root):
        raise OSError("Not inside a music folder")
    dest = os.path.join(root, TRASH, date.today().isoformat(), os.path.relpath(path, root))
    base, n = dest, 1
    while os.path.exists(dest):
        dest = f"{base} ({n})"
        n += 1
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(path, dest)
    logger.info("Music doctor moved to trash: %s → %s", path, dest)
    return dest


def cleanup_trash(roots: list[str], max_age_days: int = 30) -> int:
    cutoff = date.today() - timedelta(days=max_age_days)
    removed = 0
    for root in roots:
        tdir = os.path.join(root, TRASH)
        for name in os.listdir(tdir) if os.path.isdir(tdir) else []:
            try:
                old = datetime.strptime(name, "%Y-%m-%d").date() < cutoff
            except ValueError:
                continue
            if old:
                shutil.rmtree(os.path.join(tdir, name), ignore_errors=True)
                removed += 1
    return removed


def _hidden(rel: str) -> bool:
    return any(p.startswith(".") for p in rel.split(os.sep) if p not in (".", ""))


def _walk(root: str, skip: set[str]):
    """os.walk without hidden folders and the download folders inside the library."""
    for here, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and os.path.join(here, d) not in skip]
        yield here, dirs, files


def _empty_trees(root: str, skip: set[str]) -> list[str]:
    empty: set[str] = set()
    for here, dirs, files in os.walk(root, topdown=False):
        rel = os.path.relpath(here, root)
        if here == root or _hidden(rel) or any(_under(here, s) for s in skip):
            continue
        if not files and all(os.path.join(here, d) in empty for d in dirs):
            empty.add(here)
    return sorted(d for d in empty if os.path.dirname(d) not in empty)


def _rank(album, tracks) -> tuple:
    """Which copy of a duplicate album to keep."""
    lossless = bool(tracks) and all(t.lossless for t in tracks)
    rates = [t.bitrate for t in tracks if t.bitrate]
    return (lossless, sum(rates) / len(rates) if rates else 0, album.tracks or 0, album.size_bytes or 0)


def _describe(album, tracks) -> str:
    rates = [t.bitrate for t in tracks if t.bitrate and not t.lossless]
    q = (album.format or "?").upper() if not rates else f"{(album.format or '?').upper()} {sum(rates) // len(rates)} kbps"
    return f"{q}, {album.tracks} track{'s' if album.tracks != 1 else ''}"


def _owns_other_albums(folder: str, album_folders: set[str]) -> bool:
    return any(f != folder and _under(f, folder) for f in album_folders)


def _expected(lib: str, album, tracks) -> tuple[str, list[tuple[str, str]]]:
    """Where an album belongs, and (current path, expected name) for misnamed tracks."""
    dest = music_library.target_folder(lib, album.artist, album.title, album.year)
    multi = len({t.disc_no or 1 for t in tracks}) > 1
    wrong = []
    for t in tracks:
        name = music_library.track_name({"path": t.path, "track_no": t.track_no, "disc_no": t.disc_no,
                                          "title": t.title}, multi)
        if os.path.normpath(t.path) != os.path.normpath(os.path.join(dest, name)):
            wrong.append((t.path, name))
    return dest, wrong


def _watch_album(db, album):
    """The Watchlist entry for a library album: (artist item or None, MusicAlbum or None)."""
    from ..models import MusicAlbum, WatchlistItem
    key = music_library._norm(album.artist)
    if not key:
        return None, None
    item = next((w for w in db.query(WatchlistItem).filter(WatchlistItem.kind == "artist")
                 if music_library._norm(w.title) == key), None)
    if item is None:
        return None, None
    rows = db.query(MusicAlbum).filter(MusicAlbum.watchlist_id == item.id).all()
    hit = next((r for r in rows if album.mb_release_group_id and r.mbid == album.mb_release_group_id), None)
    if hit is None:
        title = music_library._norm(album.title)
        hit = next((r for r in rows if title and music_library._norm(r.title) == title), None)
    return item, hit


def check(db) -> dict:
    """Look through the music library. Read-only."""
    from ..models import MusicLibAlbum, MusicTrack

    roots = music_library.roots(db)
    skip = music_library.download_folders_inside(db, roots)
    lib = music_library.music_dir(db)
    issues: list[dict] = []

    # Files: leftovers and empty folders.
    for root in roots:
        for here, _dirs, files in _walk(root, skip):
            for name in files:
                if name.startswith(".") and name.lower() not in JUNK_NAMES:
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext in JUNK_EXTS or name.lower() in JUNK_NAMES:
                    issues.append(_issue("junk", os.path.join(here, name),
                                         f"{ext or name} file left over from a download", "Move to trash"))
        for folder in _empty_trees(root, skip):
            issues.append(_issue("empty", folder, "Nothing in this folder", "Remove the folder"))

    albums = db.query(MusicLibAlbum).all()
    tracks: dict[int, list] = {}
    for t in db.query(MusicTrack).all():
        tracks.setdefault(t.album_id, []).append(t)
    folders = {a.folder for a in albums}
    present = []
    for a in albums:
        if not os.path.isdir(a.folder):
            issues.append(_issue("missing", a.folder, "In the Library, but the folder isn't there any more",
                                 "Remove from the Library", album_id=a.id))
        else:
            present.append(a)

    # Duplicates: keep the best copy.
    groups: dict[tuple, list] = {}
    for a in present:
        k = (music_library._norm(a.artist), music_library._norm(a.title))
        if all(k):
            groups.setdefault(k, []).append(a)
    dup_ids = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        best = max(group, key=lambda a: _rank(a, tracks.get(a.id, [])))
        for a in group:
            if a is best:
                continue
            dup_ids.add(a.id)
            ts = tracks.get(a.id, [])
            blocked = a.folder in roots or _owns_other_albums(a.folder, folders)
            issues.append(_issue(
                "duplicate", a.folder,
                f"Another copy is kept: {best.folder} ({_describe(best, tracks.get(best.id, []))}). "
                f"This one: {_describe(a, ts)}" + (". Its folder holds other albums too, so move it by hand"
                                                   if blocked else ""),
                "Move this copy to trash", album_id=a.id, keep=best.folder, fixable=not blocked))

    for a in present:
        ts = tracks.get(a.id, [])
        flags = set(json.loads(a.issues or "[]"))
        name = f"{a.artist or '?'} – {a.title or '?'}"
        for t in ts:
            if t.duration_sec is None and (t.size_bytes or 0) > 0 and os.path.isfile(t.path):
                issues.append(_issue("broken", t.path, "The audio can't be read; it's damaged or incomplete",
                                     "Move to trash", album_id=a.id))
        if a.id in dup_ids:
            continue
        if "untagged" in flags:
            n = sum(1 for t in ts if not (t.title and t.artist and t.track_no))
            issues.append(_issue("untagged", a.folder,
                                 f"{name}: {n or 'some'} track{'s' if n != 1 else ''} without full tags",
                                 "Fix tags from MusicBrainz", album_id=a.id))
        if not a.cover:
            tried = a.cover_checked_at is not None
            issues.append(_issue("no_cover", a.folder, f"{name}: no cover image" +
                                 ("; MusicBrainz had none last time, so upload one from the album" if tried else ""),
                                 "Find artwork", album_id=a.id))
        elif ts and music_library.embedded_cover(min(ts, key=lambda t: t.path).path) is None:
            issues.append(_issue("not_embedded", a.folder, f"{name}: the files have no cover inside",
                                 "Put the cover in the files", album_id=a.id))
        if lib and "untagged" not in flags and a.artist and a.title and ts:
            dest, wrong = _expected(lib, a, ts)
            if wrong:
                where = ("Not in the music library" if not _under(a.folder, lib)
                         else f"{len(wrong)} of {len(ts)} track{'s' if len(ts) != 1 else ''} not named the Plex way"
                         if os.path.normpath(a.folder) == os.path.normpath(dest) else f"Would move to {dest}")
                issues.append(_issue("placement", a.folder, f"{name}: {where}", "Organise for Plex",
                                     album_id=a.id, target=dest))
        from . import music_follow
        for kind in ("missing_tracks", "low_bitrate"):
            if kind not in flags or music_follow.dismissed(a):
                continue
            item, wa = _watch_album(db, a)
            what = ("Some tracks are missing" if kind == "missing_tracks"
                    else f"Lossy, under {music_library.LOW_BITRATE} kbps on average")
            if wa is not None and wa.status in ("wanted", "grabbed"):
                issues.append(_issue(kind, a.folder, f"{name}: {what}. A better copy is already "
                                     f"{'wanted' if wa.status == 'wanted' else 'downloading'}",
                                     "Download a better copy", album_id=a.id, fixable=False))
                continue
            follow = "" if item is not None else f" (follows {a.artist}, new releases only)"
            issues.append(_issue(kind, a.folder, f"{name}: {what}",
                                 ("Find missing tracks" if kind == "missing_tracks" else "Upgrade to FLAC") + follow,
                                 album_id=a.id))

    order = {k: i for i, k in enumerate(LABEL)}
    issues.sort(key=lambda i: (order[i["kind"]], i["path"]))
    return {"checked_at": _now(), "roots": roots, "issues": issues[:MAX_ISSUES], "total": len(issues), "fixed": []}


def _forget(db, album_id: int) -> None:
    from ..models import MusicLibAlbum, MusicTrack
    db.query(MusicTrack).filter(MusicTrack.album_id == album_id).delete()
    a = db.get(MusicLibAlbum, album_id)
    if a is not None:
        db.delete(a)


def _organise(db, album, lib: str) -> str:
    """Organise an album, one group per album tag (a folder of loose tracks can hold several)."""
    from ..models import MusicTrack
    files = [t.path for t in db.query(MusicTrack).filter(MusicTrack.album_id == album.id) if os.path.isfile(t.path)]
    if not files:
        raise ValueError("No audio files left; scan again")
    groups: dict[tuple, list[str]] = {}
    for f in files:
        info = music_library.read(f)
        groups.setdefault((music_library._norm(info["album_artist"]), music_library._norm(info["album"])), []).append(f)
    keep = music_library.in_download_folder(db, album.folder)
    tidy = album.folder not in music_library.roots(db)      # never empty or remove a music folder itself
    dests, msgs = [], []
    for group in groups.values():
        res = music_library.organise(db, group, lib, keep_source=keep, tidy=tidy)
        if not res.get("ok"):
            raise ValueError(res.get("msg") or "Couldn't organise it")
        dests.append(res["folder"])
        msgs.append(res["msg"])
    old = album.folder
    if os.path.isdir(old):
        music_library.scan_folder(db, old)
    else:
        _forget(db, album.id)
    for d in dests:
        music_library.scan_folder(db, os.path.normpath(d))
    return "; ".join(msgs)


def fix(db, issue: dict, roots: list[str]) -> str:
    """Fix one problem now. Raises OSError/ValueError with a reason."""
    from ..models import MusicAlbum, MusicLibAlbum
    kind, path = issue["kind"], issue["path"]
    if issue.get("fixable") is False:
        raise ValueError("Nothing to fix here; see the note")
    if kind == "junk":
        if os.path.exists(path):
            trash(path, roots)
        return "Moved to trash"
    if kind == "empty":
        doctor._remove_tree(path)
        return "Removed the empty folder"
    if kind == "missing":
        _forget(db, issue["album_id"])
        db.commit()
        return "Removed from the Library"
    album = db.get(MusicLibAlbum, issue.get("album_id") or 0)
    if kind == "broken":
        if os.path.exists(path):
            trash(path, roots)
        if album is not None:
            music_library.scan_folder(db, album.folder)
            db.commit()
        return "Moved to trash"
    if album is None:
        raise ValueError("No longer in the Library; check again")
    if kind == "duplicate":
        if album.folder in roots or _owns_other_albums(album.folder, {a.folder for a in db.query(MusicLibAlbum)}):
            raise ValueError("Its folder holds other albums too; move it by hand")
        if os.path.exists(album.folder):
            trash(album.folder, roots)
        _forget(db, album.id)
        music_library.mark_duplicates(db)
        db.commit()
        return "Moved this copy to trash"
    if kind == "placement":
        lib = music_library.music_dir(db)
        if not lib or not os.path.isdir(lib):
            raise ValueError("Choose a music library in Settings → Folders first")
        msg = _organise(db, album, lib)
        music_library.mark_duplicates(db)
        db.commit()
        return msg
    if kind in ("missing_tracks", "low_bitrate"):
        wa = db.get(MusicAlbum, issue.get("watch_album_id") or 0)
        if wa is None:
            raise ValueError("No longer on the Watchlist")
        wa.status = "wanted"
        db.commit()
        return "Marked Wanted; it downloads on the next Watchlist scan"
    raise ValueError(f"Unknown problem type {kind}")


def _save(db, report: dict) -> None:
    doctor._set(db, REPORT_KEY, json.dumps(report))


def run(db, auto_fix: bool | None = None) -> dict:
    """Check, fix the safe problems (if on), and save the report."""
    with _lock:
        report = check(db)
        roots = report["roots"]
        if auto_fix if auto_fix is not None else doctor.auto_fix_enabled(db):
            for issue in [i for i in report["issues"] if i["safe"] and i["kind"] != "empty"]:
                try:
                    issue["result"] = fix(db, issue, roots)
                    report["fixed"].append(issue)
                except (OSError, ValueError) as exc:
                    issue["error"] = str(exc)
            # Folders last, looked for again: removing junk can empty them.
            report["issues"] = [i for i in report["issues"] if i["kind"] != "empty"]
            skip = music_library.download_folders_inside(db, roots)
            for root in roots:
                for folder in _empty_trees(root, skip):
                    issue = _issue("empty", folder, "Nothing in this folder", "Remove the folder")
                    try:
                        issue["result"] = fix(db, issue, roots)
                        report["fixed"].append(issue)
                    except (OSError, ValueError) as exc:
                        issue["error"] = str(exc)
                        report["issues"].append(issue)
            done = {i["id"] for i in report["fixed"]}
            report["issues"] = [i for i in report["issues"] if i["id"] not in done]
            report["total"] = len(report["issues"])
        _save(db, report)
        return report


def last_report(db) -> dict | None:
    raw = doctor._get(db, REPORT_KEY)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def fix_ids(db, ids: list[str], bind=None, person_id: int | None = None) -> dict:
    """Fix chosen problems from the last report. Tags, artwork and covers in files
    start one background bulk action (MusicBrainz is slow); the rest happen now."""
    from . import music_batch
    with _lock:
        report = last_report(db) or {"issues": [], "fixed": [], "total": 0, "roots": []}
        roots = music_library.roots(db)
        chosen = [i for i in report["issues"] if i["id"] in set(ids)]
        done, errors, started = [], [], None
        slow = [i for i in chosen if i["kind"] in BATCH]
        kinds = {i["kind"] for i in slow}
        if len(kinds) > 1:
            errors.append("Fix one kind of tag or artwork problem at a time")
        elif slow:
            action = BATCH[slow[0]["kind"]]
            try:
                music_batch.start(action, [i["album_id"] for i in slow], bind=bind, person_id=person_id)
                started = f"{music_batch.LABELS[action]} for {len(slow)} album{'s' if len(slow) != 1 else ''}"
                for i in slow:
                    i["result"] = "Started in the background"
                done += slow
            except (ValueError, RuntimeError) as exc:
                errors.append(str(exc))
        for issue in [i for i in chosen if i["kind"] not in BATCH]:
            try:
                issue["result"] = fix(db, issue, roots)
                done.append(issue)
            except (OSError, ValueError) as exc:
                db.rollback()
                errors.append(f"{os.path.basename(issue['path'].rstrip(os.sep))}: {exc}")
        done_ids = {i["id"] for i in done}
        report["issues"] = [i for i in report["issues"] if i["id"] not in done_ids]
        report["total"] = max(0, report.get("total", 0) - len(done_ids))
        report["fixed"] = ((report.get("fixed") or []) + done)[-500:]
        _save(db, report)
        return {"done": len(done), "errors": errors, "started": started, "report": report}


def scheduled_run() -> None:
    """Weekly, with the Library doctor."""
    from ..database import SessionLocal
    try:
        with SessionLocal() as db:
            r = run(db)
            cleanup_trash(r["roots"])
        logger.info("Music doctor: %d problem(s) left, %d fixed automatically", r["total"], len(r["fixed"]))
    except Exception:
        logger.exception("Music doctor failed")
