"""Following artists from the music library, and getting a better copy of an album
that's low quality or missing tracks (follow the artist if needed, want the album,
search now; the import swaps the old files into the trash)."""
import json
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

VARIOUS = {"various artists", "various", "va", "soundtrack", "unknown artist", ""}
DISMISS_DAYS = 90


class NotFound(Exception):
    """MusicBrainz has no clear match for this artist."""


def _now():
    return datetime.now(timezone.utc)


def followed(db, lib_album):
    """The Watchlist artist this library album belongs to, or None."""
    from ..models import WatchlistItem
    from .music_library import _norm
    name = _norm(lib_album.artist)
    if not name:
        return None
    for w in db.query(WatchlistItem).filter(WatchlistItem.kind == "artist"):
        if _norm(w.title) == name:
            return w
    return None


def resolve_artist(db, lib_album) -> dict:
    """{mbid, name} for the album's artist: from its release group, else an exact name
    match on MusicBrainz. Raises NotFound or musicbrainz.MusicBrainzUnavailable."""
    from . import musicbrainz
    from .music_library import _norm
    name = (lib_album.artist or "").strip()
    if _norm(name) in VARIOUS:
        raise NotFound("Compilations and Various Artists albums can't be followed")
    if lib_album.mb_release_group_id:
        hit = musicbrainz.release_group_artist(lib_album.mb_release_group_id)
        if hit and _norm(hit["name"]) not in VARIOUS:
            return hit
    for a in musicbrainz.search_artists(name, limit=5):
        if _norm(a["name"]) == _norm(name):
            return {"mbid": a["mbid"], "name": a["name"]}
    raise NotFound(f"MusicBrainz has no artist called {name}")


def follow(db, lib_album, person_id: int | None = None, start: str = "none") -> tuple[object, bool]:
    """Follow the album's artist. Returns (watchlist item, newly followed)."""
    from ..models import WatchlistItem
    from . import music, people
    item = followed(db, lib_album)
    new = False
    if item is None:
        info = resolve_artist(db, lib_album)
        item = db.query(WatchlistItem).filter(WatchlistItem.kind == "artist",
                                              WatchlistItem.mbid == info["mbid"]).first()
        if item is None:
            item = music.add_artist(db, info["mbid"], start)
            new = True
    if person_id is not None:
        people.ensure_owner(db)
        people.follow(db, item.id, person_id)
    return item, new


def watch_album(db, item, lib_album):
    """The artist's Watchlist album matching this library album, refreshing the
    discography once if it isn't there yet."""
    from ..models import MusicAlbum
    from . import music

    def find():
        rows = db.query(MusicAlbum).filter(MusicAlbum.watchlist_id == item.id).all()
        if lib_album.mb_release_group_id:
            hit = next((r for r in rows if r.mbid == lib_album.mb_release_group_id), None)
            if hit:
                return hit
        key = music._key(lib_album.title)
        return next((r for r in rows if key and music._key(r.title) == key), None)

    hit = find()
    if hit is None:
        music.refresh_albums(db, item, force=True)
        hit = find()
    return hit


def dismissed(lib_album) -> bool:
    at = lib_album.upgrade_dismissed_at
    if at is None:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return _now() - at < timedelta(days=DISMISS_DAYS)


def better_copy(db, lib_album, mode: str, person_id: int | None = None) -> dict:
    """mode "upgrade" (lossless) or "complete" (all tracks; FLAC only if the library copy
    is FLAC). Returns {status: started|wanted|not_found|error, message}."""
    from . import music
    if mode not in ("upgrade", "complete"):
        raise ValueError("mode must be upgrade or complete")
    name = " – ".join(x for x in (lib_album.artist, lib_album.title) if x) or "This album"
    try:
        item, new = follow(db, lib_album, person_id)
    except NotFound as exc:
        return {"status": "error", "message": f"{name}: {exc}."}
    wa = watch_album(db, item, lib_album)
    if wa is None:
        return {"status": "error", "message": f"{name} isn't in {item.title}'s discography on MusicBrainz."}
    lib_album.upgrade_dismissed_at = None
    if wa.status in ("have", "ignored"):
        wa.status = "wanted"
    db.commit()
    lossless = True if mode == "upgrade" else (lib_album.format or "").lower() == "flac"
    res = music.grab(db, item, wa, force=True, lossless=lossless)
    followed_note = f" Now following {item.title} (new releases only)." if new else ""
    if res["status"] == "started":
        return {"status": "started", "message": f"{res['message']}. The old copy goes to the trash once it's in.{followed_note}"}
    if res["status"] in ("not_found", "duplicate"):
        wa.status = "wanted"
        db.commit()
        return {"status": "wanted", "message": f"{res['message']} It stays Wanted and is looked for on every scan.{followed_note}"}
    return {"status": res["status"], "message": res["message"] + followed_note}


def candidates(db) -> list:
    """Library albums with low_bitrate or missing_tracks that haven't been put off."""
    from ..models import MusicLibAlbum
    out = []
    for a in db.query(MusicLibAlbum).filter(MusicLibAlbum.issues.isnot(None)):
        flags = set(json.loads(a.issues or "[]"))
        if flags & {"low_bitrate", "missing_tracks"} and not dismissed(a):
            out.append(a)
    return out


def notify_after_scan(db) -> bool:
    """One notice a day at most, when more albums could be better than last time."""
    from ..models import Setting
    from .apprise_notify import notify
    s = {r.key: r.value for r in db.query(Setting).all()}
    if (s.get("music_upgrade_notify", "true") or "").strip().lower() != "true":
        return False
    albums = candidates(db)
    low = sum(1 for a in albums if "low_bitrate" in (a.issues or ""))
    gaps = sum(1 for a in albums if "missing_tracks" in (a.issues or ""))
    total = low + gaps
    try:
        last_n = int(s.get("music_upgrade_notified_count") or 0)
    except ValueError:
        last_n = 0
    last_at = s.get("music_upgrade_notified_at")
    recent = False
    if last_at:
        try:
            recent = _now() - datetime.fromisoformat(last_at) < timedelta(hours=23)
        except ValueError:
            pass

    def save(key, value):
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value

    if total <= last_n or recent:
        if total < last_n:
            save("music_upgrade_notified_count", str(total))
            db.commit()
        return False
    parts = ([f"{low} album{'s' if low != 1 else ''} could be upgraded"] if low else []) + \
            ([f"{gaps} {'are' if gaps != 1 else 'is'} missing tracks"] if gaps else [])
    notify(s.get("apprise_url", ""), title="MediaMate — music", body=", ".join(parts) + ". Open the music doctor.",
           url="/library?lib=music&doctor=1")
    save("music_upgrade_notified_count", str(total))
    save("music_upgrade_notified_at", _now().isoformat())
    db.commit()
    return True
