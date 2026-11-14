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
_jobs = {"taste": False, "tags": False, "ratings": False}
MAX_RATING_LOOKUPS = 150           # per list refresh (about 2½ minutes at 1 request a second)
RATING_FRESH = timedelta(days=7)
MIN_VOTES = 2                      # one vote isn't a rating
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


def _learn_ratings(bind, rows: list[dict]) -> None:
    """MusicBrainz community ratings for the listed albums, a week at a time."""
    from ..models import MusicRating
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with _session(bind) as db:
        have = {r.mbid: r for r in db.query(MusicRating).filter(MusicRating.mbid.in_([x["mbid"] for x in rows]))}
        n = 0
        for x in rows:
            if x["mbid"] in have and have[x["mbid"]].checked_at == now:
                continue                                        # listed twice
            row = have.get(x["mbid"])
            if row is not None and row.checked_at and now - row.checked_at < RATING_FRESH:
                continue
            if n >= MAX_RATING_LOOKUPS:
                break
            n += 1
            try:
                value, votes = musicbrainz.rating(x["mbid"])
            except musicbrainz.MusicBrainzUnavailable as exc:
                logger.info("Rating lookup stopped: %s", exc)
                break
            if row is None:
                row = have[x["mbid"]] = MusicRating(mbid=x["mbid"])
                db.add(row)
            row.value, row.votes, row.checked_at = value, votes, now
            db.commit()


def unrated(db, rows: list[dict]) -> list[dict]:
    """Listed albums whose rating hasn't been looked up this week."""
    from ..models import MusicRating
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - RATING_FRESH
    fresh = {m for (m,) in db.query(MusicRating.mbid).filter(MusicRating.mbid.in_([r["mbid"] for r in rows]),
                                                              MusicRating.checked_at >= cutoff)}
    return [r for r in rows if r["mbid"] not in fresh]


def start_ratings(db, shown: list[dict], everything: list[dict]) -> int:
    """Look up missing ratings in the background, the albums on screen first.
    Returns how many on-screen albums are still waiting."""
    todo = unrated(db, shown)
    first = {r["mbid"] for r in todo}
    rest = [r for r in unrated(db, everything) if r["mbid"] not in first]
    if todo or rest:
        _start("ratings", _learn_ratings, db.get_bind(), todo + rest)
    return len(todo)


def ratings(db, mbids) -> dict[str, tuple[float, int]]:
    """{release group: (rating, votes)} for albums with enough votes."""
    from ..models import MusicRating
    return {r.mbid: (round(r.value, 1), r.votes) for r in
            db.query(MusicRating).filter(MusicRating.mbid.in_(list(mbids)), MusicRating.votes >= MIN_VOTES,
                                         MusicRating.value.isnot(None))}


# ── taste ───────────────────────────────────────────────────────────────────


# Not real artists: compilations, soundtracks, unknowns. Never "your taste" on their own.
VARIOUS_MBID = "89ad4ac3-39f7-470e-963a-56509c546377"     # MusicBrainz "Various Artists"
_NOT_ARTISTS = {"variousartists", "various", "va", "variousartist", "soundtrack", "soundtracks",
                "originalsoundtrack", "ost", "originalcast", "originalbroadwaycast", "unknown", "unknownartist",
                "artist", "compilation", "compilations", "karaoke", "nobody"}


def not_an_artist(name: str | None, mbids=()) -> bool:
    import re
    if VARIOUS_MBID in (mbids or ()):
        return True
    k = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    return not k or k in _NOT_ARTISTS


def _yours(db) -> tuple[Counter, dict[str, str], set[str]]:
    """(albums per artist name key, {name key: display name}, followed artist mbids).
    A compilation ("Various Artists", soundtracks) counts for the artists on its tracks,
    each getting an equal share of one album, so 51 compilations don't make "Various
    Artists" your favourite."""
    from ..models import MusicLibAlbum, MusicTrack, WatchlistItem
    counts, names = Counter(), {}
    va = []
    for a in db.query(MusicLibAlbum.id, MusicLibAlbum.artist).filter(MusicLibAlbum.artist.isnot(None)):
        if not_an_artist(a.artist):
            va.append(a.id)
            continue
        k = _name_key(a.artist)
        if k:
            counts[k] += 1
            names.setdefault(k, a.artist)
    if va:
        by_album: dict[int, list[str]] = {}
        for t in db.query(MusicTrack.album_id, MusicTrack.artist).filter(MusicTrack.album_id.in_(va)):
            if t.artist and not not_an_artist(t.artist):
                by_album.setdefault(t.album_id, []).append(t.artist)
        for artists in by_album.values():
            for name in artists:
                k = _name_key(name)
                if k:
                    counts[k] += 1 / len(artists)
                    names.setdefault(k, name)
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
            if not_an_artist(names[k]):
                continue
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


def flags(db, rows: list[dict]) -> list[dict]:
    """Copies of the releases with your status on each: following (the artist),
    status (the album on your Watchlist), in_library and watchlist_id. No lookups."""
    from ..models import MusicAlbum, MusicLibAlbum, WatchlistItem
    from . import music
    owned = set()
    for a in db.query(MusicLibAlbum.artist, MusicLibAlbum.title).filter(MusicLibAlbum.artist.isnot(None)):
        owned.add((_name_key(a.artist), music._key(a.title)))
    artists = {w.mbid: w.id for w in db.query(WatchlistItem).filter(WatchlistItem.kind == "artist") if w.mbid}
    mbids = [r["mbid"] for r in rows]
    albums = {a.mbid: a for a in db.query(MusicAlbum).filter(MusicAlbum.mbid.in_(mbids))} if mbids else {}
    out = []
    for r in rows:
        r = dict(r)
        r.setdefault("artist_mbids", [])
        r.setdefault("tags", [])
        hit = next((artists[m] for m in r["artist_mbids"] if m in artists), None)
        r["following"] = hit is not None
        r["watchlist_id"] = hit
        al = albums.get(r["mbid"])
        r["status"] = al.status if al is not None and r["following"] else None
        r["in_library"] = (_name_key(r["artist"]), music._key(r["title"])) in owned
        out.append(r)
    return out


def rank(db, rows: list[dict], for_you: bool) -> dict:
    """Releases with your status on each, ranked for you (or as they came)."""
    profile = taste(db)
    counts, _names, _followed = _yours(db)
    rated = ratings(db, [r["mbid"] for r in rows])
    out = []
    for r in flags(db, rows):
        r["rating"], r["rating_votes"] = rated.get(r["mbid"], (None, 0))
        key = _name_key(r["artist"])
        mbid = (r["artist_mbids"] or [None])[0]
        various = not_an_artist(r["artist"], r["artist_mbids"])
        have = 0 if various else int(counts.get(key, 0))
        score, why = 0.0, None
        if not various and (r["following"] or have):
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
