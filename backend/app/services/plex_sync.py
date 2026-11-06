"""Star ratings and collections, kept in step with Plex (both ways).

* MediaMate stores a file's stars (1-5) and collection names on its Library row.
* Library files are matched to Plex items by file name (then size, then the most
  matching parent folders), so Plex's own paths don't need to match MediaMate's.
* An edit in MediaMate is sent to Plex straight away; if Plex can't be reached it's
  kept (``plex_dirty``) and sent on the next sync.
* A sync (every 30 min, or "Sync now") reads Plex's values. When a file was edited
  here *and* changed in Plex since the last sync, Plex wins.
* Plex ratings are 0-10; MediaMate's stars are half that. The admin token is used,
  so ratings are the server owner's.
* Plex only allows collections on movies and shows, so an episode's collections are
  its show's.
* Adult content (protected folders, adult-looking names) is never sent to Plex.
"""
import json
import logging
import os
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from .media import store

logger = logging.getLogger(__name__)

LAST_KEY = "plex_sync_last"
CACHE_SECONDS = 600
_lock = threading.Lock()
_cache: dict = {"at": 0.0, "conf": None, "items": None, "collections": None}


class PlexError(Exception):
    pass


@dataclass
class PlexItem:
    key: str                    # the movie's or episode's ratingKey
    section: str
    kind: str                   # movie | episode
    file: str
    size: int = 0
    rating: int | None = None   # stars 1-5
    coll_key: str = ""          # where collections live: the movie, or the episode's show
    coll_type: int = 1          # 1 movie, 2 show
    collections: list[str] = field(default_factory=list)


# ── settings ────────────────────────────────────────────────────────────────


def conf() -> dict:
    s = store.get_settings(["plex_enabled", "plex_url", "plex_token", "plex_sync_enabled"])
    return {
        "url": (s.get("plex_url") or "").rstrip("/"),
        "token": s.get("plex_token") or "",
        "enabled": (s.get("plex_enabled") or "").lower() == "true"
                   and (s.get("plex_sync_enabled") or "").lower() == "true",
    }


def ready(c: dict | None = None) -> bool:
    c = c or conf()
    return bool(c["enabled"] and c["url"] and c["token"])


def _client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def _get(c: dict, path: str, **params) -> ET.Element:
    with _client() as http:
        r = http.get(c["url"] + path, params={**params, "X-Plex-Token": c["token"]},
                     headers={"Accept": "application/xml"})
    if not r.is_success:
        raise PlexError(f"Plex said HTTP {r.status_code} for {path}")
    return ET.fromstring(r.text)


def _put(c: dict, path: str, params: dict) -> None:
    with _client() as http:
        r = http.put(c["url"] + path, params={**params, "X-Plex-Token": c["token"]})
    if not r.is_success:
        raise PlexError(f"Plex said HTTP {r.status_code} for {path}")


# ── reading Plex ─────────────────────────────────────────────────────────────


def _stars(user_rating: str | None) -> int | None:
    try:
        v = float(user_rating) if user_rating not in (None, "") else 0.0
    except ValueError:
        return None
    return max(1, min(5, round(v / 2))) if v > 0 else None


def _parts(video: ET.Element):
    for part in video.iter("Part"):
        if part.get("file"):
            yield part.get("file"), int(part.get("size") or 0)


def _collections(c: dict, section: str) -> dict[str, list[str]]:
    """{item ratingKey: [collection titles]} for one section, plus all titles under ""."""
    members: dict[str, list[str]] = {"": []}
    for coll in _get(c, f"/library/sections/{section}/collections"):
        title, ck = coll.get("title"), coll.get("ratingKey")
        if not title or not ck:
            continue
        members[""].append(title)
        for child in _get(c, f"/library/collections/{ck}/children"):
            if child.get("ratingKey"):
                members.setdefault(child.get("ratingKey"), []).append(title)
    return members


def index(c: dict | None = None, force: bool = False) -> tuple[list[PlexItem], dict[str, list[str]]]:
    """Every movie, episode and music track file Plex has (a track stands for its album),
    and the collection names {"video": [...], "music": [...]}. Cached 10 min."""
    c = c or conf()
    if (not force and _cache["items"] is not None and _cache["conf"] == (c["url"], c["token"])
            and time.monotonic() - _cache["at"] < CACHE_SECONDS):
        return _cache["items"], _cache["collections"]
    items: list[PlexItem] = []
    names: dict[str, set[str]] = {"video": set(), "music": set()}
    for d in _get(c, "/library/sections").iter("Directory"):
        sec, typ = d.get("key"), d.get("type")
        if not sec or typ not in ("movie", "show", "artist"):
            continue
        colls = _collections(c, sec)
        names["music" if typ == "artist" else "video"].update(colls[""])
        if typ == "artist":
            ratings = {a.get("ratingKey"): _stars(a.get("userRating"))
                       for a in _get(c, f"/library/sections/{sec}/all", type=9).iter("Directory")}
            for t in _get(c, f"/library/sections/{sec}/all", type=10).iter("Track"):
                album = t.get("parentRatingKey") or ""
                for path, size in _parts(t):
                    items.append(PlexItem(album, sec, "album", path, size, ratings.get(album),
                                          album, 9, sorted(colls.get(album, []))))
            continue
        if typ == "movie":
            for v in _get(c, f"/library/sections/{sec}/all", type=1).iter("Video"):
                for path, size in _parts(v):
                    items.append(PlexItem(v.get("ratingKey"), sec, "movie", path, size, _stars(v.get("userRating")),
                                          v.get("ratingKey"), 1, sorted(colls.get(v.get("ratingKey"), []))))
        else:
            for v in _get(c, f"/library/sections/{sec}/all", type=4).iter("Video"):
                show = v.get("grandparentRatingKey") or ""
                for path, size in _parts(v):
                    items.append(PlexItem(v.get("ratingKey"), sec, "episode", path, size, _stars(v.get("userRating")),
                                          show, 2, sorted(colls.get(show, []))))
    _cache.update(at=time.monotonic(), conf=(c["url"], c["token"]), items=items,
                  collections={k: sorted(v) for k, v in names.items()})
    return items, _cache["collections"]


def forget_cache() -> None:
    _cache.update(at=0.0, items=None, collections=None)


# ── matching Library files to Plex items ────────────────────────────────────


def _split(path: str) -> list[str]:
    return [p.lower() for p in path.replace("\\", "/").split("/") if p]


class Matcher:
    def __init__(self, items: list[PlexItem], kind: str = "video"):
        self.by_name: dict[str, list[PlexItem]] = {}
        for it in items:
            if (it.kind == "album") != (kind == "music"):
                continue
            self.by_name.setdefault(os.path.basename(it.file.replace("\\", "/")).lower(), []).append(it)

    def find(self, path: str, size: int | None) -> PlexItem | None:
        cands = self.by_name.get(os.path.basename(path).lower(), [])
        if len(cands) <= 1:
            return cands[0] if cands else None
        mine = _split(path)

        def score(it: PlexItem):
            theirs = _split(it.file)
            same = 0
            while same < min(len(mine), len(theirs)) and mine[-1 - same] == theirs[-1 - same]:
                same += 1
            return (bool(size) and it.size == size, same)
        return max(cands, key=score)


# ── adult content stays out ─────────────────────────────────────────────────


def _guard():
    """A function path -> reason | None for files that must never reach Plex."""
    from sqlalchemy.orm import Session
    from ..models import DownloadPath
    from . import adult, folders
    with Session(bind=store._engine()) as db:
        roots = []
        for dp in db.query(DownloadPath).filter(DownloadPath.protected == True).all():  # noqa: E712
            for r in {folders.local_of(db, dp), dp.path, dp.local_path}:
                if r:
                    roots.append((os.path.normpath(os.path.abspath(r)), dp.name))
        extra = adult.extra_keywords(db)

    def check(path: str) -> str | None:
        p = os.path.normpath(os.path.abspath(path))
        for r, name in roots:
            if p == r or p.startswith(r.rstrip(os.sep) + os.sep):
                return f"it's in a protected folder ({name})"
        why = adult.looks_adult(path, extra)
        return f"it looks like adult content ({why})" if why else None
    return check


def blocked(path: str) -> str | None:
    return _guard()(path)


# ── local rows ──────────────────────────────────────────────────────────────


def _state(rating, collections) -> dict:
    return {"rating": rating or None, "collections": sorted(set(collections or []))}


def _row_state(row) -> dict:
    return _state(row["rating"], json.loads(row["collections"] or "[]"))


TABLES = {"video": "media_files", "music": "music_lib_albums"}


def _rows(kind: str) -> list:
    if kind == "video":
        return store.get_all_files()
    with store.connect() as conn:
        return conn.execute("SELECT * FROM music_lib_albums").mappings().all()


def _get_row(kind: str, row_id: int):
    if kind == "video":
        return store.get_file(row_id)
    with store.connect() as conn:
        return conn.execute("SELECT * FROM music_lib_albums WHERE id=:id", {"id": row_id}).mappings().fetchone()


def _album_tracks() -> dict[int, list[tuple[str, int]]]:
    out: dict[int, list[tuple[str, int]]] = {}
    with store.connect() as conn:
        for r in conn.execute("SELECT album_id, path, size_bytes FROM music_tracks ORDER BY path").mappings():
            out.setdefault(r["album_id"], []).append((r["path"], r["size_bytes"] or 0))
    return out


def _locate(kind: str, row, match: "Matcher", tracks: dict | None = None) -> PlexItem | None:
    if kind == "video":
        return match.find(row["path"], row["size_bytes"])
    for path, size in (tracks if tracks is not None else _album_tracks()).get(row["id"], [])[:5]:
        it = match.find(path, size)
        if it is not None:
            return it
    return None


def _label(kind: str, row) -> str:
    return row["filename"] if kind == "video" else f"{row['artist']} – {row['title']}"


def _save(file_id: int, st: dict, *, dirty: int, key=None, seen: dict | None = None, keep_key=False,
          kind: str = "video") -> None:
    sets = {"r": st["rating"], "c": json.dumps(st["collections"]) if st["collections"] else None,
            "d": dirty, "id": file_id}
    sql = f"UPDATE {TABLES[kind]} SET rating=:r, collections=:c, plex_dirty=:d"
    if not keep_key:
        sql += ", plex_key=:k"
        sets["k"] = key
    if seen is not None:
        sql += ", plex_seen=:s"
        sets["s"] = json.dumps(seen)
    with store.connect() as conn:
        conn.execute(sql + " WHERE id=:id", sets)


# ── writing to Plex ─────────────────────────────────────────────────────────


def push_rating(c: dict, it: PlexItem, stars: int | None) -> None:
    _put(c, "/:/rate", {"key": it.key, "identifier": "com.plexapp.plugins.library",
                        "rating": stars * 2 if stars else -1})


def push_collections(c: dict, it: PlexItem, wanted: list[str]) -> None:
    have, want = set(it.collections), set(wanted)
    add, remove = sorted(want - have), sorted(have - want)
    if not add and not remove:
        return
    params: dict = {"type": it.coll_type, "id": it.coll_key, "collection.locked": 1}
    for i, name in enumerate(add):
        params[f"collection[{i}].tag.tag"] = name
    if remove:
        params["collection[].tag.tag-"] = ",".join(quote(n) for n in remove)
    _put(c, f"/library/sections/{it.section}/all", params)


def _push(c: dict, it: PlexItem, st: dict) -> None:
    if st["rating"] != it.rating:
        push_rating(c, it, st["rating"])
        it.rating = st["rating"]
    if st["collections"] != sorted(it.collections):
        push_collections(c, it, st["collections"])
        it.collections = list(st["collections"])
        for other in _cache["items"] or []:          # the show's other episodes share them
            if other.coll_key == it.coll_key and other.coll_type == it.coll_type:
                other.collections = list(st["collections"])


# ── edits from the Library ──────────────────────────────────────────────────


def edit(file_id: int, *, rating: int | None | str = "keep", add: list[str] = (), remove: list[str] = (),
         collections: list[str] | None = None, kind: str = "video") -> dict:
    """Change a file's (or, kind="music", an album's) stars and/or collections, then try
    to send it to Plex.
    Returns {"ok", "rating", "collections", "plex": "sent" | "queued" | "off" | "local", "msg"}."""
    row = _get_row(kind, file_id)
    if row is None:
        raise KeyError(file_id)
    st = _row_state(row)
    if rating != "keep":
        if rating is not None and not (0 <= int(rating) <= 5):
            raise ValueError("Stars must be 0 to 5")
        st["rating"] = (int(rating) or None) if rating is not None else None
    if collections is not None or add or remove:
        names = set(collections if collections is not None else st["collections"])
        names |= {n.strip() for n in add if n and n.strip()}
        names -= {n.strip() for n in remove}
        st["collections"] = sorted(names)
    why = blocked(row["path"]) if kind == "video" else None
    if why and st["collections"]:
        raise ValueError(f"Not added to a collection: {why}")
    if why:
        _save(file_id, st, dirty=0, keep_key=True, kind=kind)
        return {"ok": True, **st, "plex": "local", "msg": f"Kept in MediaMate only: {why}"}
    _save(file_id, st, dirty=1, keep_key=True, kind=kind)
    c = conf()
    if not ready(c):
        return {"ok": True, **st, "plex": "off", "msg": "Saved (Plex sync is off)"}
    try:
        items, _ = index(c)
        it = _locate(kind, row, Matcher(items, kind))
        if it is None:
            return {"ok": True, **st, "plex": "queued",
                    "msg": f"Saved; Plex hasn't found this {'file' if kind == 'video' else 'album'} yet"}
        # Only what was edited here is sent; the rest follows Plex.
        if rating == "keep":
            st["rating"] = it.rating
        if collections is None:      # add/remove apply to what Plex has now
            names = set(it.collections) | {n.strip() for n in add if n and n.strip()}
            st["collections"] = sorted(names - {n.strip() for n in remove})
        _push(c, it, st)
        _save(file_id, st, dirty=0, key=it.key, seen=st, kind=kind)
        return {"ok": True, **st, "plex": "sent", "msg": "Saved and sent to Plex"}
    except (PlexError, httpx.HTTPError, ET.ParseError) as exc:
        logger.warning("Plex sync: couldn't send now (%s); will retry", exc)
        return {"ok": True, **st, "plex": "queued", "msg": "Saved; it goes to Plex on the next sync"}


# ── the two-way sync ────────────────────────────────────────────────────────


def sync() -> dict:
    """Read Plex, send unsent edits, and store the result. Never raises."""
    c = conf()
    if not ready(c):
        return {"ok": False, "msg": "Turn on Plex and 'Sync ratings & collections' first"}
    if not _lock.acquire(blocking=False):
        return {"ok": False, "msg": "A sync is already running"}
    try:
        return _sync(c)
    except (PlexError, httpx.HTTPError, ET.ParseError) as exc:
        result = {"ok": False, "msg": f"Couldn't reach Plex: {exc}", "at": _now()}
        store.set_setting(LAST_KEY, json.dumps(result))
        return result
    finally:
        _lock.release()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sync(c: dict) -> dict:
    items, _ = index(c, force=True)
    check = _guard()
    counts = {"matched": 0, "unmatched": 0, "pulled": 0, "pushed": 0, "skipped": 0}
    errors: list[str] = []
    _sync_kind(c, "video", items, check, counts, errors)
    _sync_kind(c, "music", items, check, counts, errors)
    result = {"ok": not errors, "at": _now(), **counts, "errors": errors[:5],
              "msg": f"{counts['matched']} matched, {counts['pulled']} updated from Plex, "
                     f"{counts['pushed']} sent to Plex"}
    store.set_setting(LAST_KEY, json.dumps(result))
    logger.info("Plex sync: %s", result["msg"])
    return result


def _sync_kind(c: dict, kind: str, items, check, counts: dict, errors: list) -> None:
    match = Matcher(items, kind)
    tracks = _album_tracks() if kind == "music" else None
    for row in _rows(kind):
        if kind == "video" and check(row["path"]):
            counts["skipped"] += 1
            continue
        it = _locate(kind, row, match, tracks)
        if it is None:
            counts["unmatched"] += 1
            continue
        counts["matched"] += 1
        plex = _state(it.rating, it.collections)
        local = _row_state(row)
        seen = json.loads(row["plex_seen"]) if row["plex_seen"] else None
        if row["plex_dirty"] and seen is None:
            # Edited before the first sync: keep both sides' values.
            local = _state(local["rating"] or plex["rating"], local["collections"] + plex["collections"])
        if row["plex_dirty"] and (seen is None or seen == plex) and local != plex:
            try:
                _push(c, it, local)
                counts["pushed"] += 1
                _save(row["id"], local, dirty=0, key=it.key, seen=local, kind=kind)
            except (PlexError, httpx.HTTPError) as exc:
                errors.append(f"{_label(kind, row)}: {exc}")
            continue
        if local != plex:
            counts["pulled"] += 1
        _save(row["id"], plex, dirty=0, key=it.key, seen=plex, kind=kind)   # Plex wins


def status() -> dict:
    raw = store.get_setting(LAST_KEY, "")
    try:
        last = json.loads(raw) if raw else None
    except ValueError:
        last = None
    with store.connect() as conn:
        pending = sum(conn.execute(f"SELECT COUNT(*) AS n FROM {t} WHERE plex_dirty=1").mappings().fetchone()["n"]
                      for t in TABLES.values())
    return {"enabled": ready(), "last": last, "pending": pending}


def collection_names(kind: str = "video") -> list[str]:
    """Plex's collection names (when reachable) plus every name used in the Library."""
    names: set[str] = set()
    if ready():
        try:
            names.update(index()[1].get(kind, []))
        except (PlexError, httpx.HTTPError, ET.ParseError):
            pass
    for row in _rows(kind):
        if row["collections"]:
            names.update(json.loads(row["collections"]))
    return sorted(names, key=str.lower)
