"""Import an AuTorrent and/or FileSplitter database into MediaMate.

Uploads land in a private temp folder under a random token (inspect), then
run() copies them in. The kind of each file is recognised by its tables, so the
user can drop both files in one go.

AuTorrent's schema is MediaMate's own (MediaMate grew out of it), so the
uploaded copy is first brought up to date with run_migrations() and then copied
table by table. FileSplitter's files/jobs map onto media_files/media_jobs.
Every table is merged, never replaced: ids are remapped, duplicates are skipped
by their natural key, and the current database is backed up beforehand.
"""
import logging
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "mediamate-import")
UPLOAD_TTL_SEC = 3600
_TOKEN_RE = re.compile(r"^[a-f0-9]{32}$")
SQLITE_MAGIC = b"SQLite format 3\x00"

AUTORRENT_TABLES = ("watchlist", "downloads", "settings")
FILESPLITTER_TABLES = ("files", "jobs", "settings")

# Never copied: they belong to this install.
SKIP_SETTINGS = {"session_secret", "secret_key"}
# AuTorrent settings are copied only when MediaMate has the same setting (its
# defaults list), minus ones that describe AuTorrent itself rather than your
# preferences. Version, update and upgrade state, caches and "last run" markers
# are not settings MediaMate knows, so they never come across.
AUTORRENT_NEVER = {
    "app_source_url",   # AuTorrent's repo: MediaMate would check the wrong releases
    "qbit_category",    # keep MediaMate's own label, so the two apps' torrents stay apart
}
# FileSplitter settings that MediaMate keeps under the same key.
FS_SETTING_KEYS = {
    "scene_threshold", "min_scene_duration", "scene_method", "x265_crf", "x265_preset",
    "target_resolution", "encoder_backend", "max_workers", "ffprobe_timeout",
    "black_min_duration", "black_pix_th", "split_min_duration", "split_min_size",
    "split_keywords", "split_scene_method", "split_min_episode_gap",
    "split_black_min_duration", "split_freeze_min_duration", "split_episode_count",
}
IMPORTED_JOB_REASON = "Imported from FileSplitter"


class ImportFailed(ValueError):
    """A user-facing problem with an uploaded file."""


# ── Uploads ───────────────────────────────────────────────────────────────────

def _cleanup_old_uploads() -> None:
    if not os.path.isdir(UPLOAD_DIR):
        return
    cutoff = time.time() - UPLOAD_TTL_SEC
    for name in os.listdir(UPLOAD_DIR):
        path = os.path.join(UPLOAD_DIR, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def upload_path(token: str) -> str:
    if not _TOKEN_RE.match(token or ""):
        raise ImportFailed("Unknown upload — please choose the file again")
    path = os.path.join(UPLOAD_DIR, f"{token}.db")
    if not os.path.isfile(path):
        raise ImportFailed("That upload has expired — please choose the file again")
    return path


def save_upload(fileobj) -> str:
    """Store an uploaded file privately; returns its token. Rejects non-SQLite files."""
    _cleanup_old_uploads()
    os.makedirs(UPLOAD_DIR, mode=0o700, exist_ok=True)
    token = secrets.token_hex(16)
    path = os.path.join(UPLOAD_DIR, f"{token}.db")
    with open(path, "wb") as out:
        shutil.copyfileobj(fileobj, out, length=1024 * 1024)
    with open(path, "rb") as f:
        if f.read(16) != SQLITE_MAGIC:
            os.remove(path)
            raise ImportFailed("That isn't a SQLite database (expected autorrent.db or filesplitter.db)")
    return token


def _tables(path: str) -> set[str]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.DatabaseError as exc:
        raise ImportFailed(f"The database can't be read ({exc})")
    finally:
        con.close()


def detect_kind(path: str) -> str:
    tables = _tables(path)
    if "media_files" in tables or "journeys" in tables:
        raise ImportFailed("This looks like a MediaMate database — use Settings → Data management → Restore instead")
    if all(t in tables for t in AUTORRENT_TABLES):
        return "autorrent"
    if all(t in tables for t in FILESPLITTER_TABLES):
        return "filesplitter"
    raise ImportFailed("Not an AuTorrent or FileSplitter database")


def _count(con, table: str, where: str = "") -> int:
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0]
    except sqlite3.DatabaseError:
        return 0


def _importable_settings(con: sqlite3.Connection, kind: str) -> int:
    """How many of the upload's settings would be imported (not its version or update state)."""
    try:
        keys = {r[0] for r in con.execute("SELECT key FROM settings")}
    except sqlite3.DatabaseError:
        return 0
    if kind == "filesplitter":
        return len(keys & FS_SETTING_KEYS)
    from ..routers.settings import DEFAULTS
    return len({k for k in keys if k in DEFAULTS and k not in AUTORRENT_NEVER} - SKIP_SETTINGS)


def inspect_upload(token: str) -> dict:
    """What's in an uploaded database, without changing anything."""
    path = upload_path(token)
    kind = detect_kind(path)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        if kind == "autorrent":
            counts = {
                "watchlist": _count(con, "watchlist"),
                "episodes": _count(con, "watchlist_episodes"),
                "downloads": _count(con, "downloads"),
                "history": _count(con, "download_history"),
                "folders": _count(con, "download_paths"),
                "profiles": _count(con, "quality_profiles"),
                "settings": _importable_settings(con, "autorrent"),
            }
            sample = []
        else:
            counts = {
                "files": _count(con, "files"),
                "jobs": _count(con, "jobs"),
                "settings": _importable_settings(con, "filesplitter"),
            }
            row = con.execute("SELECT value FROM settings WHERE key='total_saved_bytes'").fetchone()
            counts["saved_bytes"] = int(row[0]) if row and str(row[0]).isdigit() else 0
            paths = [r[0] for r in con.execute("SELECT path FROM files ORDER BY id LIMIT 200")]
            sample = paths[:3]
            counts["files_found_here"] = sum(os.path.exists(p) for p in paths)
            counts["files_checked"] = len(paths)
    finally:
        con.close()
    return {"token": token, "kind": kind, "counts": counts, "sample_paths": sample}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _columns(engine, table: str) -> list[str]:
    return [c["name"] for c in inspect(engine).get_columns(table)]


def _rows(src_engine, table: str) -> list[dict]:
    with src_engine.connect() as c:
        return [dict(r) for r in c.execute(text(f"SELECT * FROM {table}")).mappings()]


def _insert(db: Session, table: str, row: dict, columns: list[str]) -> int:
    data = {k: v for k, v in row.items() if k in columns and k != "id"}
    cols = ", ".join(data)
    params = ", ".join(f":{k}" for k in data)
    return db.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({params})"), data).lastrowid


def _autorrent_settings(rows: list[tuple[str, str]], profile_map: dict[int, int]) -> list[tuple[str, str]]:
    """The AuTorrent settings worth keeping, with IDs pointed at the imported rows."""
    from ..routers.settings import DEFAULTS

    keep = []
    for key, value in rows:
        if key not in DEFAULTS or key in AUTORRENT_NEVER:
            continue
        if key == "default_profile_id":
            try:
                value = str(profile_map[int(value)])
            except (TypeError, ValueError, KeyError):
                continue  # no such profile came across
        keep.append((key, value))
    return keep


def _merge_settings(db: Session, rows: list[tuple[str, str]], overwrite: bool,
                    choices: dict[str, bool] | None = None) -> int:
    """Copy settings in. `choices` (from the comparison) says per key whether to take
    the imported value; keys it doesn't mention follow `overwrite` (fill blanks only)."""
    changed = 0
    for key, value in rows:
        if key in SKIP_SETTINGS or value is None:
            continue
        cur = db.execute(text("SELECT value FROM settings WHERE key=:k"), {"k": key}).first()
        if choices is not None and key in choices:
            if not choices[key]:
                continue
        elif cur is not None and cur[0] not in (None, "") and not overwrite:
            continue
        db.execute(text("INSERT INTO settings (key, value) VALUES (:k, :v) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value"), {"k": key, "v": value})
        changed += 1
    return changed


def backup_current(db_path: str | None) -> str | None:
    """Snapshot the live database next to itself before importing."""
    if not db_path or not os.path.isfile(db_path):
        return None
    dest = os.path.join(os.path.dirname(db_path),
                        f"pre-import-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db")
    src = sqlite3.connect(db_path)
    try:
        out = sqlite3.connect(dest)
        with out:
            src.backup(out)
        out.close()
    finally:
        src.close()
    return dest


# ── AuTorrent ─────────────────────────────────────────────────────────────────

def _import_autorrent(db: Session, path: str, overwrite_settings: bool,
                      choices: dict[str, bool] | None = None) -> dict:
    from ..migrations import run_migrations

    work = path + ".work"
    shutil.copyfile(path, work)
    src = create_engine(f"sqlite:///{work}")
    try:
        run_migrations(bind=src)  # bring an older AuTorrent schema up to date (on the copy)
        dst = db.get_bind()
        report: dict[str, int] = {}

        def table(name: str):
            return _rows(src, name), _columns(dst, name)

        # Download folders — same path = same folder.
        path_map: dict[int, int] = {}
        rows, cols = table("download_paths")
        added = 0
        for r in rows:
            hit = db.execute(text("SELECT id FROM download_paths WHERE path=:p"), {"p": r["path"]}).first()
            if hit:
                path_map[r["id"]] = hit[0]
            else:
                if db.execute(text("SELECT 1 FROM download_paths WHERE is_default=1")).first():
                    r = {**r, "is_default": False}
                path_map[r["id"]] = _insert(db, "download_paths", r, cols)
                added += 1
        report["folders"] = added

        # Quality profiles — same name = same profile.
        profile_map: dict[int, int] = {}
        rows, cols = table("quality_profiles")
        added = 0
        for r in rows:
            hit = db.execute(text("SELECT id FROM quality_profiles WHERE name=:n"), {"n": r["name"]}).first()
            if hit:
                profile_map[r["id"]] = hit[0]
            else:
                profile_map[r["id"]] = _insert(db, "quality_profiles", r, cols)
                added += 1
        report["profiles"] = added

        # Watchlist — same show title = same item.
        wl_map: dict[int, int] = {}
        rows, cols = table("watchlist")
        added = 0
        for r in rows:
            hit = db.execute(text("SELECT id FROM watchlist WHERE lower(title)=lower(:t)"), {"t": r["title"]}).first()
            if hit:
                wl_map[r["id"]] = hit[0]
                continue
            r = {**r, "download_path_id": path_map.get(r.get("download_path_id")),
                 "profile_id": profile_map.get(r.get("profile_id"))}
            wl_map[r["id"]] = _insert(db, "watchlist", r, cols)
            added += 1
        report["watchlist"] = added

        # Episodes already grabbed (unique per show/season/episode).
        rows, cols = table("watchlist_episodes")
        added = 0
        for r in rows:
            wid = wl_map.get(r["watchlist_id"])
            if wid is None or db.execute(text(
                    "SELECT 1 FROM watchlist_episodes WHERE watchlist_id=:w AND season=:s AND episode=:e"),
                    {"w": wid, "s": r["season"], "e": r["episode"]}).first():
                continue
            _insert(db, "watchlist_episodes", {**r, "watchlist_id": wid}, cols)
            added += 1
        report["episodes"] = added

        # Downloads — same torrent = same download. The Download session hooks don't
        # fire for these raw inserts; journeys are backfilled afterwards.
        rows, cols = table("downloads")
        added = 0
        for r in rows:
            if r.get("torrent_hash") and db.execute(
                    text("SELECT 1 FROM downloads WHERE torrent_hash=:h"), {"h": r["torrent_hash"]}).first():
                continue
            _insert(db, "downloads", {**r, "watchlist_id": wl_map.get(r.get("watchlist_id"))}, cols)
            added += 1
        report["downloads"] = added

        rows, cols = table("download_history")
        added = 0
        for r in rows:
            if db.execute(text(
                    "SELECT 1 FROM download_history WHERE name=:n AND added_at IS :a "
                    "AND torrent_hash IS :h"), {"n": r.get("name"), "a": r.get("added_at"),
                                                 "h": r.get("torrent_hash")}).first():
                continue
            _insert(db, "download_history", {**r, "watchlist_id": wl_map.get(r.get("watchlist_id"))}, cols)
            added += 1
        report["history"] = added

        rows, cols = table("trakt_seen")
        added = 0
        for r in rows:
            if db.execute(text("SELECT 1 FROM trakt_seen WHERE trakt_id=:i"), {"i": r["trakt_id"]}).first():
                continue
            db.execute(text(f"INSERT INTO trakt_seen ({', '.join(c for c in r if c in cols)}) VALUES "
                            f"({', '.join(':' + c for c in r if c in cols)})"),
                       {k: v for k, v in r.items() if k in cols})
            added += 1
        report["trakt_seen"] = added

        with src.connect() as c:
            settings = [(k, v) for k, v in c.execute(text("SELECT key, value FROM settings"))]
        report["settings"] = _merge_settings(db, _autorrent_settings(settings, profile_map),
                                             overwrite_settings, choices)
        return report
    finally:
        src.dispose()
        try:
            os.remove(work)
        except OSError:
            pass


# ── FileSplitter ──────────────────────────────────────────────────────────────

def _rewrite(path: str, rewrite: tuple[str, str] | None) -> str:
    if not rewrite:
        return path
    old, new = rewrite[0].rstrip("/"), rewrite[1].rstrip("/")
    if path == old or path.startswith(old + "/"):
        return new + path[len(old):]
    return path


def _import_filesplitter(db: Session, path: str, overwrite_settings: bool,
                         rewrite: tuple[str, str] | None, choices: dict[str, bool] | None = None) -> dict:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        dst = db.get_bind()
        file_cols, job_cols = _columns(dst, "media_files"), _columns(dst, "media_jobs")
        file_map: dict[int, int] = {}
        added = missing = 0
        for r in (dict(x) for x in con.execute("SELECT * FROM files ORDER BY id")):
            r["path"] = _rewrite(r["path"], rewrite)
            r["filename"] = r.get("filename") or os.path.basename(r["path"])
            if r.get("status") in ("queued", "processing"):
                r["status"] = "pending"  # its job doesn't come across as runnable
            hit = db.execute(text("SELECT id FROM media_files WHERE path=:p"), {"p": r["path"]}).first()
            if hit:
                file_map[r["id"]] = hit[0]
                continue
            file_map[r["id"]] = _insert(db, "media_files", r, file_cols)
            added += 1
            missing += not os.path.exists(r["path"])

        jobs = 0
        for r in (dict(x) for x in con.execute("SELECT * FROM jobs ORDER BY id")):
            fid = file_map.get(r.get("file_id"))
            if fid is None:
                continue
            if r.get("status") in ("queued", "running"):
                r["status"] = "cancelled"
                r["log_tail"] = "Not finished before the move to MediaMate"
            r = {**r, "file_id": fid, "reason": IMPORTED_JOB_REASON, "journey_id": None}
            _insert(db, "media_jobs", r, job_cols)
            jobs += 1

        settings = [(r["key"], r["value"]) for r in con.execute("SELECT key, value FROM settings")
                    if r["key"] in FS_SETTING_KEYS]
        changed = _merge_settings(db, settings, overwrite_settings, choices)

        saved = con.execute("SELECT value FROM settings WHERE key='total_saved_bytes'").fetchone()
        saved_bytes = int(saved[0]) if saved and str(saved[0]).isdigit() else 0
        if saved_bytes:
            db.execute(text(
                "INSERT INTO settings (key, value) VALUES ('total_saved_bytes', :s) "
                "ON CONFLICT(key) DO UPDATE SET value=CAST(CAST(value AS INTEGER) + :n AS TEXT)"),
                {"s": str(saved_bytes), "n": saved_bytes})
        return {"files": added, "files_missing_here": missing, "jobs": jobs,
                "settings": changed, "saved_bytes": saved_bytes}
    finally:
        con.close()


# ── Run ───────────────────────────────────────────────────────────────────────

SETTING_LABELS = {
    "qbit_host": "qBittorrent host", "qbit_port": "qBittorrent port", "qbit_username": "qBittorrent user",
    "qbit_password": "qBittorrent password", "scan_interval_minutes": "Watchlist scan every (minutes)",
    "min_seeds": "Minimum seeders", "remove_on_complete": "Remove finished torrents",
    "jackett_url": "Prowlarr / Jackett URL", "jackett_api_key": "Prowlarr / Jackett API key",
    "apprise_url": "Apprise URL", "tmdb_api_key": "TMDB API key", "default_profile_id": "Default quality profile",
    "default_indexer": "Default search source", "browse_country": "Browse country",
    "trakt_client_id": "Trakt client ID", "trakt_username": "Trakt username",
    "plex_url": "Plex URL", "plex_token": "Plex token", "jellyfin_url": "Jellyfin URL",
    "jellyfin_api_key": "Jellyfin API key", "digest_smtp_password": "Digest email password",
    "ui_password": "Login password", "stall_hours": "Stalled after (hours)",
    "x265_crf": "x265 quality (CRF)", "x265_preset": "x265 preset", "target_resolution": "Target resolution",
    "encoder_backend": "Encoder", "max_workers": "Simultaneous jobs", "ffprobe_timeout": "ffprobe timeout (s)",
    "scene_threshold": "Scene threshold", "scene_method": "Scene method", "split_keywords": "Split keywords",
}


def _profile_names(con) -> dict[str, str]:
    try:
        return {str(i): n for i, n in con.execute("SELECT id, name FROM quality_profiles")}
    except sqlite3.DatabaseError:
        return {}


def compare_settings(bind, tokens: list[str]) -> dict:
    """Every importable setting whose value differs from what this install uses now.

    `import_by_default` mirrors what Import does with no choices: fill a setting
    you haven't set, keep one you have. Secrets are never shown, only whether they differ.
    """
    from ..routers.settings import DEFAULTS
    from .media import config as media_config
    from .secret_settings import MASK, SECRET_KEYS

    with Session(bind=bind) as db:
        current = {k: v for k, v in db.execute(text("SELECT key, value FROM settings"))}
        here_profiles = {str(i): n for i, n in db.execute(text("SELECT id, name FROM quality_profiles"))}

    def effective(key: str) -> tuple[str, bool]:
        """(value in use here, whether you set it yourself)."""
        if current.get(key) not in (None, ""):
            return str(current[key]), True
        if key in DEFAULTS:
            return DEFAULTS[key], False
        attr = media_config.SETTINGS_MAP.get(key, (None,))[0]
        value = getattr(media_config, attr, "") if attr else ""
        return (",".join(map(str, value)) if isinstance(value, list) else str(value)), False

    secret = SECRET_KEYS | {"tpdb_api_key"}
    rows, same = [], 0
    for token in tokens:
        path = upload_path(token)
        kind = detect_kind(path)
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            raw = [(k, v) for k, v in con.execute("SELECT key, value FROM settings") if v is not None]
            names = _profile_names(con)
        finally:
            con.close()
        if kind == "autorrent":
            incoming = [(k, v) for k, v in raw if k in DEFAULTS and k not in AUTORRENT_NEVER and k not in SKIP_SETTINGS]
        else:
            incoming = [(k, v) for k, v in raw if k in FS_SETTING_KEYS]
        for key, value in incoming:
            here, set_here = effective(key)
            value = str(value)
            if key == "default_profile_id":  # IDs differ between installs: compare by name
                here, value = here_profiles.get(here, ""), names.get(value, "")
                if not value:
                    continue
            if value.strip().lower() == here.strip().lower():
                same += 1
                continue
            hidden = key in secret
            rows.append({
                "key": key, "source": kind,
                "label": SETTING_LABELS.get(key) or key.replace("_", " ").capitalize(),
                "here": (MASK if here else "") if hidden else here,
                "incoming": MASK if hidden else value,
                "secret": hidden,
                "set_here": set_here,
                "import_by_default": not set_here,
            })
    rows.sort(key=lambda r: (r["source"], r["key"]))
    return {"differences": rows, "same": same}


def suggest_paths(bind, tokens: list[str]) -> dict:
    """Recommended folder change for FileSplitter's files and path mappings for qBittorrent."""
    from . import path_suggest
    from .media import config as media_config

    by_kind = {detect_kind(upload_path(t)): upload_path(t) for t in tokens}
    with Session(bind=bind) as db:
        existing = [(r[0], r[1]) for r in db.execute(text("SELECT remote_prefix, local_prefix FROM path_mappings"))]
    return path_suggest.suggest(by_kind.get("autorrent"), by_kind.get("filesplitter"),
                                list(media_config.MEDIA_PATHS), existing)


def _add_mappings(db: Session, mappings: list[tuple[str, str]]) -> int:
    added = 0
    for remote, local in mappings:
        remote, local = remote.rstrip("/") or "/", local.rstrip("/") or "/"
        if db.execute(text("SELECT 1 FROM path_mappings WHERE remote_prefix IN (:r, :rs)"),
                      {"r": remote, "rs": remote + "/"}).first():
            continue  # never replace a mapping you set yourself
        db.execute(text("INSERT INTO path_mappings (remote_prefix, local_prefix) VALUES (:r, :l)"),
                   {"r": remote, "l": local})
        added += 1
    return added


def run(bind, tokens: list[str], overwrite_settings: bool = False,
        rewrite: tuple[str, str] | None = None, db_path: str | None = None,
        mappings: list[tuple[str, str]] = (), choices: dict[str, bool] | None = None) -> dict:
    """Import every uploaded database in one transaction. Returns a report per file."""
    uploads = [(t, upload_path(t)) for t in tokens]
    kinds = [(t, p, detect_kind(p)) for t, p in uploads]
    if len({k for _, _, k in kinds}) != len(kinds):
        raise ImportFailed("Upload at most one AuTorrent and one FileSplitter database")

    backup = backup_current(db_path)
    results = []
    with Session(bind=bind) as db:
        try:
            # AuTorrent first, so its settings land before FileSplitter's encoder ones.
            for token, path, kind in sorted(kinds, key=lambda x: x[2] != "autorrent"):
                if kind == "autorrent":
                    report = _import_autorrent(db, path, overwrite_settings, choices)
                else:
                    report = _import_filesplitter(db, path, overwrite_settings, rewrite, choices)
                results.append({"token": token, "kind": kind, "imported": report})
            added_mappings = _add_mappings(db, list(mappings))
            db.commit()
        except Exception:
            db.rollback()
            raise

    for _, path in uploads:
        try:
            os.remove(path)
        except OSError:
            pass
    _after_import(bind)
    return {"ok": True, "backup": backup, "results": results, "mappings_added": added_mappings}


def _after_import(bind) -> None:
    """Apply imported settings and show imported downloads on Activity."""
    from . import journeys
    from .media import config as media_config

    try:
        media_config.load_from_db()
    except Exception:
        logger.exception("Reloading media settings after import failed")
    try:
        from . import folders
        with Session(bind=bind) as db:
            folders.refresh(db)  # imported download folders are scanned too
    except Exception:
        logger.exception("Refreshing folders after import failed")
    try:
        with Session(bind=bind) as db:
            journeys.backfill(db)
    except Exception:
        logger.exception("Journey backfill after import failed")
    try:
        from .scheduler import _get_interval, update_interval
        update_interval(_get_interval())
    except Exception:
        pass  # scheduler not running (tests) — the next restart picks the interval up
