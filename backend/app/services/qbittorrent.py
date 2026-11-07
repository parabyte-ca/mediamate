import logging
import re
import urllib.parse

import qbittorrentapi

from ..database import SessionLocal
from ..models import Setting
from .torrent_meta import url_to_magnet

logger = logging.getLogger(__name__)


def _get_settings() -> dict[str, str]:
    db = SessionLocal()
    try:
        return {s.key: s.value for s in db.query(Setting).all()}
    finally:
        db.close()


def _get_client() -> qbittorrentapi.Client:
    s = _get_settings()
    host = s.get("qbit_host", "localhost")
    port = int(s.get("qbit_port", "8080"))
    username = s.get("qbit_username", "admin")
    logger.debug("Connecting to qBittorrent at %s:%s as %s", host, port, username)
    client = qbittorrentapi.Client(
        host=host,
        port=port,
        username=username,
        password=s.get("qbit_password", ""),
    )
    try:
        client.auth_log_in()
    except qbittorrentapi.LoginFailed:
        # qBittorrent 5.x returns HTTP 204 (empty body) on successful login.
        # Older library versions expect "Ok." in the body and raise LoginFailed
        # even though the session cookie was set correctly. Verify by making a
        # real API call — if that succeeds the connection is fine.
        client.app_version()
    return client


def _hash_from_magnet(magnet: str) -> str | None:
    match = re.search(r"btih:([a-fA-F0-9]{40,64})", magnet, re.IGNORECASE)
    return match.group(1).lower() if match else None


def add_torrent(magnet: str, save_path: str, category: str | None = None) -> str | None:
    # Indexer proxies may hand us a .torrent URL; resolve it to a magnet first so
    # the info-hash is known for duplicate detection and status tracking.
    if magnet.lower().startswith(("http://", "https://")):
        magnet, _ = url_to_magnet(magnet)
    client = _get_client()
    kwargs: dict = {"urls": magnet, "save_path": save_path}
    if category:
        kwargs["category"] = category
    res = client.torrents_add(**kwargs)
    # qBittorrent answers "Ok." or "Fails." (5.1+ may answer with a JSON summary).
    if isinstance(res, str) and res.strip().lower().startswith("fail"):
        raise AddFailed("qBittorrent refused the torrent (it may already be in qBittorrent)")
    return _hash_from_magnet(magnet)


class AddFailed(Exception):
    """qBittorrent didn't take the torrent, or errored straight away."""


# Words in a qBittorrent error that mean the save folder is the problem (permissions,
# a path its container doesn't have, a full or read-only disk), not the torrent.
_FOLDER_PROBLEM = re.compile(r"(?i)permission|denied|read-only|read only|no such file|cannot create|couldn't create|"
                             r"could not create|not a directory|no space|disk full|create directory|save path|couldn.t save")


def is_folder_problem(reason: str | None) -> bool:
    return bool(reason and _FOLDER_PROBLEM.search(reason))


def error_reason(info_hash: str | None, name: str | None = None) -> str | None:
    """Why qBittorrent put a torrent in its error state, from qBittorrent's own log
    (newest matching warning or critical line). Falls back to naming the save folder."""
    try:
        client = _get_client()
    except Exception:
        return None
    keys = [k.lower() for k in (info_hash, name) if k]
    save = None
    if info_hash:
        try:
            props = client.torrents_properties(torrent_hash=info_hash)
            save = props.get("save_path") if hasattr(props, "get") else getattr(props, "save_path", None)
        except Exception:
            pass
    if save:
        keys.append(save.lower().rstrip("/"))
    try:
        lines = client.log_main(normal=False, info=False, warning=True, critical=True) or []
    except Exception:
        lines = []
    for line in reversed(list(lines)[-300:]):
        msg = str(line.get("message", "") if isinstance(line, dict) else getattr(line, "message", ""))
        if msg and any(k and k in msg.lower() for k in keys):
            return msg[:400]
    return f"qBittorrent couldn't save to {save}" if save else None


CONFIRM_WAIT = 6.0          # seconds; tests set 0


def confirm_added(info_hash: str | None, name: str | None = None, wait: float | None = None) -> None:
    """Give qBittorrent a moment to start a new torrent; raise AddFailed with its reason
    if the torrent errors straight away (most often: it can't write the save folder)."""
    import time
    from .qbit_states import ERROR_STATES
    if not info_hash:
        return
    deadline = time.monotonic() + (CONFIRM_WAIT if wait is None else wait)
    while True:
        st = get_torrent_status(info_hash)
        if st and st.get("status") in ERROR_STATES:
            raise AddFailed(error_reason(info_hash, name) or f"qBittorrent reported '{st['status']}'")
        if (st and st.get("status") not in ("metaDL", "checkingResumeData", "allocating")) or time.monotonic() >= deadline:
            return
        time.sleep(1.0)


def _torrent_to_dict(t) -> dict:
    return {
        "status": t.state,
        "progress": round(t.progress, 4),
        "size": t.size,
        "dlspeed": t.dlspeed,
        "eta": t.eta,
    }


def get_torrent_status(info_hash: str) -> dict | None:
    try:
        client = _get_client()
        torrents = client.torrents_info(torrent_hashes=info_hash)
        if torrents:
            return _torrent_to_dict(torrents[0])
    except Exception:
        pass
    return None


def get_all_torrent_statuses() -> dict[str, dict] | None:
    """Fetch every torrent's status in one API call.

    Returns a dict mapping lowercase info_hash -> status dict, or None
    if qBittorrent is unreachable.  Callers can safely treat a hash
    that is absent from the returned dict as "removed from qBittorrent"
    (distinct from None which means "could not connect at all").
    """
    try:
        client = _get_client()
        return {t.hash.lower(): _torrent_to_dict(t) for t in client.torrents_info()}
    except Exception as e:
        logger.warning("Failed to fetch all torrent statuses: %s", e)
        return None


def get_content_path(info_hash: str) -> str | None:
    """Where qBittorrent stored a torrent's data (file or top folder), as qBittorrent sees it."""
    try:
        torrents = _get_client().torrents_info(torrent_hashes=info_hash)
        if torrents:
            return getattr(torrents[0], "content_path", None) or None
    except Exception as e:
        logger.warning("Could not read content path for %s: %s", info_hash, e)
    return None


def remove_torrent(info_hash: str, delete_files: bool = False) -> None:
    """Remove a torrent from qBittorrent, optionally deleting downloaded files."""
    client = _get_client()
    client.torrents_delete(delete_files=delete_files, torrent_hashes=info_hash)


# qBittorrent 5.0 added app/getDirectoryContent in Web API 2.11.2. Older versions
# answer 404 for the unknown endpoint, the same as 5.x does for a missing folder, so
# the version decides what a 404 means.
_DIR_API = (2, 11, 2)


def _api_version(client) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in str(client.app_web_api_version()).split("."))
    except Exception:
        return ()


def directory_exists(path: str) -> bool | None:
    """Whether qBittorrent can see `path`. None when it can't be asked: not
    connected, or qBittorrent 4.x, which has no way to check."""
    try:
        client = _get_client()
    except Exception:
        return None
    if _api_version(client) < _DIR_API:
        return None
    try:
        client.app_get_directory_content(directory_path=path)
        return True
    except Exception as e:
        # qbittorrentapi's HTTP errors carry the status; 5.x answers 404/409/400
        # for a folder that doesn't exist or can't be listed.
        return False if getattr(e, "http_status_code", None) in (400, 404, 409) else None


def known_folders(max_torrent_folders: int = 10) -> list[dict] | None:
    """The folders qBittorrent saves into: its default save path, each category's
    save path, and the folders its torrents are in (most used first).
    Returns [{"path", "why", "category"}], or None when qBittorrent can't be reached."""
    try:
        client = _get_client()
    except Exception:
        return None
    found: dict[str, dict] = {}

    def note(path, why, category=None):
        path = (path or "").strip().rstrip("/")
        if not path:
            return
        entry = found.setdefault(path, {"path": path, "why": [], "category": category})
        if why not in entry["why"]:
            entry["why"].append(why)
        entry["category"] = entry["category"] or category

    try:
        note(client.app_preferences().get("save_path"), "qBittorrent's default save folder")
    except Exception:
        pass
    try:
        for name, cat in (client.torrents_categories() or {}).items():
            note((cat or {}).get("savePath") or (cat or {}).get("save_path"), f"category “{name}”", name)
    except Exception:
        pass
    try:
        counts: dict[str, int] = {}
        for t in client.torrents_info():
            sp = (getattr(t, "save_path", "") or "").rstrip("/")
            if sp:
                counts[sp] = counts.get(sp, 0) + 1
        for sp, n in sorted(counts.items(), key=lambda kv: -kv[1])[:max_torrent_folders]:
            note(sp, f"{n} torrent{'s' if n != 1 else ''} saved here")
    except Exception:
        pass
    return list(found.values())


def test_connection() -> tuple[bool, str]:
    try:
        client = _get_client()
        version = client.app_version()
        return True, f"Connected to qBittorrent {version}"
    except Exception as e:
        msg = str(e) or type(e).__name__
        logger.error("qBittorrent connection failed: %s: %s", type(e).__name__, msg)
        return False, f"{type(e).__name__}: {msg}" if str(e) else type(e).__name__
