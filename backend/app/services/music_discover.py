"""Browse → Music: new releases and what's popular (ListenBrainz, free, no key), ranked
"for you" by a taste profile built from your music library and the artists you follow.

* New releases: ListenBrainz "fresh releases" (the last few weeks and the next few).
* Popular:      ListenBrainz site-wide top release groups this week.
* Taste:        MusicBrainz tags (genres) of your artists, weighted by how many of their
                albums you have; followed artists count too. Looked up once and cached
                (music_artist_tags), one MusicBrainz request a second, in the background.
* For you:      artists you have or follow first, then releases whose artist shares your
                tags. Releases with nothing in common are hidden unless you ask for all.
"""
import json
import logging
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx

from . import musicbrainz

logger = logging.getLogger(__name__)

LB = "https://api.listenbrainz.org/1"
CACHE_FOR = 6 * 3600
TAG_RETRY = timedelta(days=90)
MAX_TASTE_ARTISTS = 300
MAX_RELEASE_LOOKUPS = 120          # artist tag lookups after a list refresh (about 2 minutes)
TOP_TAGS = 40
SYNC = False                       # tests run lookups inline

_cache: dict[str, tuple[float, list]] = {}
_jobs = {"taste": False, "tags": False}
_lock = threading.Lock()


class Unavailable(Exception):
    pass


def _lb(path: str, **params) -> dict:
    try:
        with httpx.Client(timeout=20.0, headers={"User-Agent": musicbrainz._agent()}) as http:
            r = http.get(f"{LB}{path}", params=params)
    except httpx.HTTPError as exc:
        raise Unavailable(str(exc)) from exc
    if not r.is_success:
        raise Unavailable(f"ListenBrainz answered HTTP {r.status_code}")
    return r.json()


def _cover(item: dict) -> str | None:
    if item.get("caa_release_mbid") and item.get("caa_id"):
        return f"https://coverartarchive.org/release/{item['caa_release_mbid']}/{item['caa_id']}-250.jpg"
    rg = item.get("release_group_mbid")
    return musicbrainz.cover_url(rg) if rg else None


def _fresh() -> list[dict]:
    data = _lb("/explore/fresh-releases/", days=21, past="true", future="true", sort="release_date")
    out = []
    for r in (data.get("payload") or {}).get("releases") or []:
        kind = (r.get("release_group_primary_type") or "").title()
        if kind and kind not in ("Album", "Ep"):
            continue
        if r.get("release_group_secondary_type"):           # compilations, live, remixes…
            continue
        if not r.get("release_group_mbid"):
            continue
        out.append({"mbid": r["release_group_mbid"], "title": r.get("release_name") or "",
                    "artist": r.get("artist_credit_name") or "", "artist_mbids": r.get("artist_mbids") or [],
                    "date": r.get("release_date"), "type": "EP" if kind == "Ep" else (kind or "Album"),
                    "cover": _cover(r), "listens": r.get("listen_count") or 0,
                    "tags": [t.lower() for t in r.get("release_tags") or [] if isinstance(t, str)]})
    out.sort(key=lambda r: r["date"] or "", reverse=True)
    return out


def _popular() -> list[dict]:
    data = _lb("/stats/sitewide/release-groups", range="week", count=100)
    out = []
    for r in (data.get("payload") or {}).get("release_groups") or []:
        if not r.get("release_group_mbid"):
            continue
        out.append({"mbid": r["release_group_mbid"], "title": r.get("release_group_name") or "",
                    "artist": r.get("artist_name") or "", "artist_mbids": r.get("artist_mbids") or [],
                    "date": None, "type": "Album", "cover": _cover(r), "listens": r.get("listen_count") or 0,
                    "tags": []})
    return out


def releases(kind: str, bind=None) -> list[dict]:
    """New ("new") or popular ("popular") releases, cached for 6 hours. Raises Unavailable."""
    now = time.monotonic()
    hit = _cache.get(kind)
    if hit and now - hit[0] < CACHE_FOR:
        return hit[1]
    rows = _fresh() if kind == "new" else _popular()
    _cache[kind] = (now, rows)
    _start("tags", _learn_release_artists, bind, rows)
    return rows


# ── artist tags (cached) ────────────────────────────────────────────────────


def _name_key(name: str | None) -> str:
    from .music_library import _norm
    return _norm(name)


def _tags_of(raw) -> dict[str, int]:
    return {t["name"].lower(): int(t.get("count") or 1) for t in raw or [] if t.get("name") and int(t.get("count") or 1) > 0}


def _lookup(name: str | None, mbid: str | None) -> tuple[str | None, dict[str, int]]:
    """(mbid, {tag: votes}) for an artist from MusicBrainz."""
    if mbid:
        a = musicbrainz._get(f"/artist/{mbid}", inc="tags")
        return mbid, _tags_of(a.get("tags"))
    data = musicbrainz._get("/artist", query=f'artist:"{name}"', limit=1)
    hits = data.get("artists") or []
    if not hits or int(hits[0].get("score") or 0) < 90:
        return None, {}
    return hits[0]["id"], _tags_of(hits[0].get("tags"))


def artist_tags(db, name: str | None, mbid: str | None = None, lookup: bool = False) -> dict[str, int] | None:
    """Cached tags for an artist; looks them up when allowed. None = not known yet."""
    from ..models import MusicArtistTag
    key = _name_key(name) if name else None
    q = db.query(MusicArtistTag)
    row = (q.filter(MusicArtistTag.mbid == mbid).first() if mbid else None) or \
          (q.filter(MusicArtistTag.name_key == key).first() if key else None)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if row is not None:
        tags = json.loads(row.tags or "{}")
        if tags or (row.checked_at and now - row.checked_at < TAG_RETRY) or not lookup:
            return tags
    if not lookup:
        return None
    try:
        got_mbid, tags = _lookup(name, mbid)
    except musicbrainz.MusicBrainzUnavailable as exc:
        logger.info("Tag lookup for %s unavailable: %s", name or mbid, exc)
        return None
    if row is None:
        row = MusicArtistTag(name_key=key or (got_mbid or mbid or ""), name=name)
        db.add(row)
    row.mbid = got_mbid or mbid or row.mbid
    row.tags, row.checked_at = json.dumps(tags), now
    db.commit()
    return tags


def _start(job: str, fn, *args) -> None:
    with _lock:
        if _jobs[job]:
            return
        _jobs[job] = True

    def run():
        try:
            fn(*args)
        except Exception:
            logger.exception("Music discovery job %s failed", job)
        finally:
            _jobs[job] = False

    if SYNC:
        run()
    else:
        threading.Thread(target=run, name=f"music-{job}", daemon=True).start()


def _session(bind):
    from sqlalchemy.orm import Session
    from .media import store
    return Session(bind=bind or store._engine())


def _learn_release_artists(bind, rows: list[dict]) -> None:
    with _session(bind) as db:
        n = 0
        for r in rows:
            if n >= MAX_RELEASE_LOOKUPS:
                break
            mbid = (r["artist_mbids"] or [None])[0]
            if artist_tags(db, r["artist"], mbid) is None:
                artist_tags(db, r["artist"], mbid, lookup=True)
                n += 1


# ── taste ───────────────────────────────────────────────────────────────────


def _yours(db) -> tuple[Counter, dict[str, str], set[str]]:
    """(albums per artist name key, {name key: display name}, followed artist mbids)."""
    from ..models import MusicLibAlbum, WatchlistItem
    counts, names = Counter(), {}
    for a in db.query(MusicLibAlbum.artist).filter(MusicLibAlbum.artist.isnot(None)):
        k = _name_key(a.artist)
        if k:
            counts[k] += 1
            names.setdefault(k, a.artist)
    followed = set()
    for w in db.query(WatchlistItem).filter(WatchlistItem.kind == "artist"):
        k = _name_key(w.title)
        counts[k] += 2                                   # following says a lot
        names.setdefault(k, w.title)
        if w.mbid:
            followed.add(w.mbid)
    return counts, names, followed


def build_taste(bind=None) -> None:
    """Look up tags for your artists (most albums first). Slow: run in the background."""
    from ..models import WatchlistItem
    with _session(bind) as db:
        counts, names, _ = _yours(db)
        mbid_of = {_name_key(w.title): w.mbid for w in db.query(WatchlistItem).filter(WatchlistItem.kind == "artist")}
        for k, _n in counts.most_common(MAX_TASTE_ARTISTS):
            if artist_tags(db, names[k], mbid_of.get(k)) is None:
                artist_tags(db, names[k], mbid_of.get(k), lookup=True)


def start_taste(bind=None) -> bool:
    if _jobs["taste"]:
        return False
    _start("taste", build_taste, bind)
    return True


def taste(db) -> dict[str, float]:
    """{tag: weight} (weights add up to 1) from the artists you have and follow."""
    counts, names, _ = _yours(db)
    total: Counter = Counter()
    for k, n in counts.items():
        tags = artist_tags(db, names[k])
        if not tags:
            continue
        votes = sum(tags.values()) or 1
        for t, c in tags.items():
            total[t] += n * c / votes
    top = dict(total.most_common(TOP_TAGS))
    s = sum(top.values()) or 1
    return {t: w / s for t, w in top.items()}


def rank(db, rows: list[dict], for_you: bool) -> dict:
    """Releases with your status on each, ranked for you (or as they came)."""
    from ..models import MusicAlbum, MusicLibAlbum, WatchlistItem
    from . import music
    profile = taste(db)
    counts, _names, followed = _yours(db)
    owned = {}
    for a in db.query(MusicLibAlbum).filter(MusicLibAlbum.artist.isnot(None)):
        owned.setdefault((_name_key(a.artist), music._key(a.title)), a.format)
    artists = {w.mbid: w for w in db.query(WatchlistItem).filter(WatchlistItem.kind == "artist") if w.mbid}
    albums = {a.mbid: a for a in db.query(MusicAlbum)}
    out = []
    for r in rows:
        r = dict(r)
        key = _name_key(r["artist"])
        mbid = (r["artist_mbids"] or [None])[0]
        r["following"] = bool(set(r["artist_mbids"]) & followed)
        al = albums.get(r["mbid"])
        r["status"] = al.status if al is not None and r["following"] else None
        r["in_library"] = (key, music._key(r["title"])) in owned
        have = counts.get(key, 0)
        score, why = 0.0, None
        if r["following"] or have:
            score = 10.0 + have
            why = "You follow them" if r["following"] else f"You have {have} album{'s' if have != 1 else ''} by them"
        else:
            tags = dict.fromkeys(r["tags"], 1) or artist_tags(db, r["artist"], mbid) or {}
            votes = sum(tags.values()) or 1
            shared = sorted(((profile[t] * c / votes, t) for t, c in tags.items() if t in profile), reverse=True)
            score = sum(s for s, _ in shared)
            if shared:
                why = "Matches: " + ", ".join(t for _, t in shared[:3])
        r["score"], r["why"] = round(score, 4), why
        out.append(r)
    if for_you and profile:
        out = [r for r in out if r["score"] > 0]
        out.sort(key=lambda r: -r["score"])
    learning = sum(1 for r in rows if artist_tags(db, r["artist"], (r["artist_mbids"] or [None])[0]) is None)
    return {"releases": out, "taste": [t for t in list(profile)[:12]], "taste_ready": bool(profile),
            "learning": learning, "building": _jobs["taste"]}


def add(db, artist_mbid: str, release_group_mbid: str, title: str = "", date: str | None = None,
        kind: str | None = None) -> dict:
    """Browse → Add: follow the artist (only future releases) and want this album now."""
    from ..models import MusicAlbum, WatchlistItem
    from . import music
    item = (db.query(WatchlistItem)
            .filter(WatchlistItem.kind == "artist", WatchlistItem.mbid == artist_mbid).first())
    followed_now = item is None
    if item is None:
        item = music.add_artist(db, artist_mbid, "none")
    album = (db.query(MusicAlbum)
             .filter(MusicAlbum.watchlist_id == item.id, MusicAlbum.mbid == release_group_mbid).first())
    if album is None:
        album = MusicAlbum(watchlist_id=item.id, mbid=release_group_mbid, title=title or "?", album_type=kind,
                           release_date=date or None, status="wanted")
        db.add(album)
    if album.status in ("ignored", "wanted"):
        album.status = "wanted"
    db.commit()
    res = {"status": album.status, "message": ""}
    if album.status == "wanted" and music.released(album):
        try:
            res = music.grab(db, item, album)
        except Exception as exc:
            logger.warning("Browse add: grab failed for %s: %s", album.title, exc)
            res = {"status": "wanted", "message": "It downloads on the next Watchlist scan."}
    elif album.status == "wanted":
        res = {"status": "waiting", "message": f"It downloads once it's out ({album.release_date})."}
    lead = f"Following {item.title}. " if followed_now else ""
    return {"ok": True, "watchlist_id": item.id, "album_status": album.status,
            "message": lead + (res.get("message") or "")}
