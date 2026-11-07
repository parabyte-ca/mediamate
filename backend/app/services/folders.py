"""One list of folders for downloads and the media engine.

Each row in ``download_paths`` can be a place qBittorrent downloads into
(``downloads``), a place the media engine scans and encodes (``scan``), or both.
qBittorrent may see a folder under a different path than this container does, so
a row keeps both: ``path`` as qBittorrent sees it, and ``local_path`` here. When
``local_path`` is empty it comes from the path mappings.

The media engine's folder list (``media.config.MEDIA_PATHS``) is derived from
the scanned rows by ``refresh()``. It used to be its own setting (``media_paths``,
or ``MEDIA_PATHS`` in .env); ``merge_once()`` folds that into these rows on
upgrade, and ``adopt_env()`` picks up folders added later with setup.sh --media.
"""
import logging
import os
import shutil
import uuid

from sqlalchemy.orm import Session

from ..models import DownloadPath, Setting

logger = logging.getLogger(__name__)

MERGED_KEY = "folders_merged"
REMOVED_KEY = "folders_removed"      # .env folders you deleted here; not re-added on restart


def _norm(path: str | None) -> str:
    return os.path.normpath(path) if path else ""


def _get(db: Session, key: str) -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    return (row.value or "") if row else ""


def _set(db: Session, key: str, value: str) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def local_of(db: Session, dp: DownloadPath) -> str:
    """Where this folder is inside MediaMate's container."""
    if dp.local_path:
        return _norm(dp.local_path)
    from .pipeline import map_path
    return _norm(map_path(db, dp.path))


def scan_roots(db: Session) -> list[str]:
    """The folders the media engine works in: scanned rows, outermost only."""
    paths = sorted({local_of(db, dp) for dp in db.query(DownloadPath).filter(DownloadPath.scan.is_(True))} - {""})
    roots: list[str] = []
    for p in paths:  # sorted, so a parent always comes before its children
        if not any(p == r or p.startswith(r.rstrip("/") + "/") for r in roots):
            roots.append(p)
    return roots


def refresh(db: Session) -> list[str]:
    """Point the media engine at the scanned folders; keep the old setting in step."""
    from .media import config

    roots = scan_roots(db)
    config.MEDIA_PATHS = roots
    _set(db, "media_paths", config.serialize("media_paths", roots))
    db.commit()
    return roots


def _covered(db: Session, local: str) -> DownloadPath | None:
    """The row whose folder here is exactly `local`, if any."""
    for dp in db.query(DownloadPath).all():
        if local_of(db, dp) == local:
            return dp
    return None


def _add_library(db: Session, local: str) -> DownloadPath:
    dp = DownloadPath(name=os.path.basename(local.rstrip("/")) or local, path=local, local_path=local,
                      downloads=False, scan=True, is_default=False)
    db.add(dp)
    db.flush()
    return dp


def merge_once(db: Session) -> None:
    """Fold the old, separate media folders into the Folders list (first start after upgrade).

    * Download folders keep downloading, and are scanned when they exist here.
    * Each old media folder that isn't one of them becomes a library-only folder,
      so nothing that was scanned before stops being scanned.
    """
    if _get(db, MERGED_KEY):
        return
    from .media import config

    old_roots = [_norm(p) for p in config.MEDIA_PATHS if p]
    # The built-in default (/media with no MEDIA_PATHS set) only counts if it exists.
    explicit = bool(_get(db, "media_paths")) or "MEDIA_PATHS" in os.environ
    for dp in db.query(DownloadPath).all():
        dp.downloads = True
        dp.scan = os.path.isdir(local_of(db, dp))
    for root in old_roots:
        if not explicit and not os.path.isdir(root):
            continue
        hit = _covered(db, root)
        if hit:
            hit.scan = True
        else:
            _add_library(db, root)
    _set(db, MERGED_KEY, "1")
    db.commit()
    logger.info("Folders: merged %d media folder(s) into the Folders list", len(old_roots))


def adopt_env(db: Session) -> None:
    """Folders named in MEDIA_PATHS (.env, setup.sh --media) appear in the list."""
    from .media import config

    raw = os.environ.get("MEDIA_PATHS", "")
    removed = set(filter(None, _get(db, REMOVED_KEY).split(",")))
    for path in (_norm(p) for p in config._parse_paths(raw)):
        if path and path not in removed and not _covered(db, path):
            _add_library(db, path)
            logger.info("Folders: added %s from MEDIA_PATHS", path)
    db.commit()


def forget_env(db: Session, local: str) -> None:
    """A folder you deleted here stays deleted even though .env still names it."""
    from .media import config

    env = {_norm(p) for p in config._parse_paths(os.environ.get("MEDIA_PATHS", ""))}
    if local in env:
        removed = set(filter(None, _get(db, REMOVED_KEY).split(",")))
        removed.add(local)
        _set(db, REMOVED_KEY, ",".join(sorted(removed)))


def set_scan_paths(db: Session, paths: list[str]) -> list[str]:
    """The old media-folders setting, applied to the Folders list (kept for API compatibility):
    exactly these folders are scanned; folders not yet listed are added as library folders."""
    wanted = [_norm(p) for p in paths if str(p).strip()]
    for dp in db.query(DownloadPath).all():
        dp.scan = local_of(db, dp) in wanted
    for p in wanted:
        if not _covered(db, p):
            _add_library(db, p)
    db.commit()
    return refresh(db)


def download_folder(db: Session, path_id: int | None) -> DownloadPath | None:
    """The folder to send a torrent to: the one chosen, if it still takes downloads,
    otherwise the default download folder."""
    if path_id:
        dp = db.query(DownloadPath).filter(DownloadPath.id == path_id).first()
        if dp is not None and dp.downloads:
            return dp
    return (db.query(DownloadPath)
            .filter(DownloadPath.is_default.is_(True), DownloadPath.downloads.is_(True)).first())


def startup(db: Session) -> list[str]:
    merge_once(db)
    adopt_env(db)
    return refresh(db)


# ── Folder test (Settings → Folders → Test) ───────────────────────────────────

_SCAN_CAP = 5000


def _source(db: Session, dp: DownloadPath) -> str:
    """Where MediaMate's path for this folder comes from, in words."""
    if dp.local_path:
        return "set as Path here"
    from ..models import PathMapping
    from .pipeline import map_path
    mapped = map_path(db, dp.path)
    if mapped != dp.path:
        for m in db.query(PathMapping).all():
            remote = m.remote_prefix.rstrip("/")
            if dp.path == remote or dp.path.startswith(remote + "/"):
                return f"from the path mapping {m.remote_prefix} → {m.local_prefix}"
    return "the same path qBittorrent uses"


def _nearest_existing(path: str) -> str:
    parent = path
    while parent and parent != "/" and not os.path.isdir(parent):
        parent = os.path.dirname(parent.rstrip("/")) or "/"
    return parent or "/"


def _count_videos(root: str, exts=None, max_depth: int = 1) -> tuple[int, bool]:
    """Videos (or ``exts``) in the folder and ``max_depth`` levels down; True when the
    count stopped at the cap."""
    from .media import config
    exts = exts or config.VIDEO_EXTENSIONS
    found = seen = 0
    stack = [(root, 0)]
    while stack:
        here, depth = stack.pop()
        try:
            with os.scandir(here) as it:
                for e in it:
                    seen += 1
                    if seen > _SCAN_CAP:
                        return found, True
                    if e.name.startswith("."):
                        continue
                    if e.is_dir(follow_symlinks=False):
                        if depth < max_depth:
                            stack.append((e.path, depth + 1))
                    elif os.path.splitext(e.name)[1].lower() in exts:
                        found += 1
        except OSError:
            if here == root:
                raise
    return found, False


def _volume_line(host: str, container: str) -> str:
    """A docker-compose volume entry; quoted when a path has a space or colon-like risk."""
    entry = f"{host}:{container}"
    return f'- "{entry}"' if any(c in entry for c in " #") else f"- {entry}"


def _mount_root(path: str) -> str:
    """The folder a container most likely has mounted for `path`: /mnt/video for
    /mnt/video/tv shows, /downloads for /downloads/tv."""
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "/"
    depth = 2 if parts[0] in ("mnt", "media", "data", "srv", "storage", "volume1", "share") and len(parts) > 1 else 1
    return "/" + "/".join(parts[:depth])


def _recent_write_failure(db: Session, dp: DownloadPath, days: int = 7):
    """The newest download into this folder that qBittorrent couldn't save (permissions,
    a missing path, a full disk) in the last week. qBittorrent has no way to test
    writing, so a real failure is the evidence."""
    from datetime import datetime, timedelta, timezone
    from ..models import Download
    from .qbittorrent import is_folder_problem
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    rows = (db.query(Download).filter(Download.status.in_(["error", "failed"]), Download.error_detail.isnot(None),
                                      Download.created_at >= since)
            .order_by(Download.id.desc()).limit(200).all())
    want = _norm(dp.path)
    return next((d for d in rows if _norm(d.download_path or "") == want and is_folder_problem(d.error_detail)), None)


def _qbit_write_hint(path: str) -> dict:
    return {
        "kind": "qbit-write",
        "text": (f"qBittorrent can see {path} but can't write to it, so downloads there error straight away. "
                 "On TrueNAS: Datasets → the dataset holding this folder → Edit permissions (ACL) → add qBittorrent's "
                 "user (the apps user, uid 568, unless you changed it) with Modify, and apply it to child folders. "
                 "Then, on the Watchlist, press download on the album again."),
        "steps": [
            {"label": "Check from qBittorrent's side (on the host; use your qBittorrent container's name). "
                      "It prints \"writable\" once it's fixed",
             "copy": f"docker exec qbittorrent sh -c 'mkdir -p \"{path}\" && touch \"{path}/.mm-write-test\" "
                     f"&& rm \"{path}/.mm-write-test\" && echo writable'"},
        ],
    }


def _same_as_qbit_hint(qbit_path: str, local: str) -> dict:
    """qBittorrent can see the folder and MediaMate can't: downloads land in
    qBittorrent's mount, so give MediaMate the same host folder at the same path."""
    root = _mount_root(qbit_path) if local == qbit_path else _mount_root(local)
    return {
        "kind": "same-as-qbit",
        "text": (f"qBittorrent saves into {qbit_path} inside its own container, and MediaMate doesn't have that "
                 f"folder mounted, so downloads finish where only qBittorrent can see them. Find the host folder "
                 f"qBittorrent mounts at {root} (step 1; on TrueNAS it's also under the qBittorrent app's Storage "
                 f"settings), put it on the left of the volume line, add that line under volumes in "
                 f"docker-compose.override.yml, then run: docker compose up -d. Using the same path inside both "
                 f"containers means no path mapping is needed."),
        "steps": [
            {"label": "1. Show qBittorrent's mounts (on the host; use your qBittorrent container's name)",
             "copy": "docker inspect qbittorrent --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'"},
            {"label": "2. Volume line for MediaMate", "copy": _volume_line("/host/folder/from/step/1", root)},
        ],
    }


def check(db: Session, dp: DownloadPath, low_threshold: int = 0) -> dict:
    """Can MediaMate (and qBittorrent) use this folder? A checklist plus the likeliest fix."""
    from .disk_space import is_low

    local = local_of(db, dp)
    checks: list[dict] = []
    hint = None

    def add(key, label, status, detail):
        checks.append({"key": key, "label": label, "status": status, "detail": detail})

    add("path", "Path here", "ok", f"{local or '(none)'}, {_source(db, dp)}")

    visible = bool(local) and os.path.isdir(local)
    if not visible:
        parent = _nearest_existing(local or "/")
        add("visible", "Visible to MediaMate", "fail",
            f"{local} doesn't exist in MediaMate's container"
            + (f"; {parent} does" if parent not in ("/", "") else ""))
        mapped = _source(db, dp).startswith("from the path mapping")
        hint = ({"kind": "mapping",
                 "text": f"Check the path mapping: it turns {dp.path} into {local}, which doesn't exist here."}
                if mapped else
                {"kind": "mount",
                 "text": "Mount the folder into MediaMate: add this under volumes in docker-compose.override.yml, "
                         "replacing the left side with the folder's path on the host, then run: docker compose up -d",
                 "steps": [{"label": "Volume line", "copy": _volume_line("/path/on/host", local)}]})
        for key, label in (("read", "Readable"), ("write", "Writable"), ("space", "Free space")):
            add(key, label, "skip", "Not checked: the folder isn't visible")
    else:
        add("visible", "Visible to MediaMate", "ok", "Found")
        try:
            if (dp.target or "") == "music":
                from .music_library import AUDIO_EXTS
                n, capped = _count_videos(local, AUDIO_EXTS, max_depth=2)   # Artist/Album/track
                add("read", "Readable", "ok",
                    f"{n}{'+' if capped else ''} audio file{'s' if n != 1 else ''} in the folder and two levels down")
            else:
                n, capped = _count_videos(local)
                add("read", "Readable", "ok",
                    f"{n}{'+' if capped else ''} video{'s' if n != 1 else ''} in the folder and one level down")
        except OSError as e:
            add("read", "Readable", "fail", f"Can't list it: {e.strerror or e}")
            hint = hint or {"kind": "permissions",
                            "text": f"MediaMate runs as user {os.getuid()}; give that user read access on the host."}

        probe = os.path.join(local, f".mediamate-write-test-{uuid.uuid4().hex[:8]}")
        try:
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            add("write", "Writable", "ok", "MediaMate can create and delete files here")
        except OSError as e:
            needs = dp.scan or dp.downloads
            add("write", "Writable", "fail" if needs else "warn",
                f"Read-only ({e.strerror or e})"
                + (": encode, split, trim and trash need to write here" if dp.scan
                   else ": previews work, but nothing can be changed"))
            hint = hint or {"kind": "permissions",
                            "text": f"Remove ':ro' from this folder's volume in docker-compose.override.yml, or give "
                                    f"user {os.getuid()} write access on the host."}

        try:
            usage = shutil.disk_usage(local)
            low = is_low(usage.free, low_threshold)
            add("space", "Free space", "warn" if low else "ok",
                f"{usage.free / 1e9:.1f} GB free of {usage.total / 1e9:.1f} GB" + (" (low)" if low else ""))
        except OSError as e:
            add("space", "Free space", "skip", f"Unknown ({e.strerror or e})")

    if dp.downloads:
        from . import qbittorrent
        seen = qbittorrent.directory_exists(dp.path)
        if seen is True:
            add("qbit", "qBittorrent sees it", "ok", f"qBittorrent can open {dp.path}")
            if not visible and hint and hint["kind"] == "mount":
                hint = _same_as_qbit_hint(dp.path, local)
        elif seen is False:
            add("qbit", "qBittorrent sees it", "fail", f"qBittorrent can't find {dp.path}")
            hint = hint or {"kind": "qbit",
                            "text": "Use the path exactly as qBittorrent sees it (its container's path), "
                                    "or mount the folder into qBittorrent."}
        else:
            add("qbit", "qBittorrent sees it", "skip",
                "Couldn't ask qBittorrent (not connected, or version 4.x, which has no way to check)")
        failed = _recent_write_failure(db, dp)
        if failed is not None:
            add("qbit_write", "qBittorrent can write here", "fail",
                f"A download into {dp.path} failed: {failed.error_detail}")
            hint = hint or _qbit_write_hint(dp.path)

    ok = not any(c["status"] == "fail" for c in checks)
    return {"ok": ok, "local": local, "checks": checks, "hint": hint}


# ── Folders qBittorrent already uses (Settings → Folders → "Found in qBittorrent") ──

COMMON_MOUNTS = ("/media", "/data", "/downloads", "/mnt", "/storage", "/srv")


def suggestions(db: Session) -> dict:
    """qBittorrent's folders that aren't in the list yet, with where each one is
    inside MediaMate (through a path mapping, or found by its folder names)."""
    from . import qbittorrent
    from .path_suggest import _locate
    from .pipeline import map_path

    known = qbittorrent.known_folders()
    if known is None:
        return {"connected": False, "suggestions": []}

    rows = db.query(DownloadPath).all()
    listed = {_norm(dp.path) for dp in rows} | {local_of(db, dp) for dp in rows}
    roots = []
    for r in [local_of(db, dp) for dp in rows] + list(COMMON_MOUNTS):
        for cand in (r, os.path.dirname(r.rstrip("/"))):
            if cand and cand != "/" and os.path.isdir(cand) and cand not in roots:
                roots.append(cand)

    out = []
    taken = {dp.name.lower() for dp in rows}
    for k in known:
        path = _norm(k["path"])
        if path in listed:
            continue
        local, how = map_path(db, path), "path mapping"
        if local == path:
            how = "same path"
        if not os.path.isdir(local):
            hit = _locate(path, roots, os.path.isdir)
            if hit:
                old, new = hit
                local, how = _norm(new.rstrip("/") + path[len(old.rstrip("/")):]), "found by name"
        name = k.get("category") or os.path.basename(path) or path
        name = name[:1].upper() + name[1:]
        if name.lower() in taken:   # e.g. a library folder already called Movies
            name = f"{name} (qBittorrent)"
        taken.add(name.lower())
        out.append({
            "path": path,
            "name": name,
            "why": k["why"],
            "local": local,
            "visible_here": os.path.isdir(local),
            "how": how,
        })
    out.sort(key=lambda s: (not s["visible_here"], s["path"]))
    return {"connected": True, "suggestions": out}
