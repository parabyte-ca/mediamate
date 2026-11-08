"""Music on the Watchlist: follow an artist, download each album once it's out.

The video mechanics, applied to music:
* An "artist" watchlist item (``kind="artist"``) holds the artist's MusicBrainz id.
  Its albums (MusicBrainz release groups) are rows in ``music_albums``.
* Following "new releases" marks albums already out as ignored. "Latest" also wants
  the most recent one, and "Everything" wants the whole back catalogue.
* Each scan refreshes the album list at most once a day, then looks for wanted albums
  whose release date has come. The target is FLAC. In the first 48 hours after release
  only FLAC is taken; after that the best copy goes (MP3 320 or V0, then plain MP3).
  Low-bitrate copies (128-256 kbps) are never taken.
* Albums download into the music download folder with the qBittorrent category
  ``qbit_music_category`` (default "music"). A finished download marks the album "have".
"""
import logging
import re
from datetime import date, datetime, timedelta, timezone

from .candidates import WATCHLIST_LOCK, failed_hashes, failed_titles, result_hash

logger = logging.getLogger(__name__)

FALLBACK_AFTER_HOURS = 48
REFRESH_EVERY = timedelta(hours=24)
MAX_GRABS_PER_SCAN = 3
_TRY_TOP = 5
STARTS = ("new", "latest", "all", "none")   # none: only future releases (Browse → Add wants one album)

_VIDEO = re.compile(r"(?i)\bS\d{1,2}E\d{1,3}\b|\b(?:480|576|720|1080|2160)[pi]\b|\bx26[45]\b|\bh\.?26[45]\b|"
                    r"\bhevc\b|\bblu-?ray\b|\bweb-?(?:dl|rip)\b|\bbdrip\b|\bdvdrip\b|\bhdtv\b|\bmkv\b|\bmp4\b")
_BUNDLE = re.compile(r"(?i)\b(?:discography|discografia|anthology|complete (?:albums|works|collection)|"
                     r"\d+ albums|box ?set)\b")
_LOSSLESS = re.compile(r"(?i)\b(?:flac|alac|lossless|24[ -]?bit|16[ -]?bit|hi-?res|wav)\b")
_HIGH = re.compile(r"(?i)\b(?:320(?:\s?k(?:bps)?)?|v0)\b")
_LOW = re.compile(r"(?i)\b(?:96|112|128|160|192|224|256)\s?k(?:bps)?\b|\b(?:128|192|256)\b(?=.*\bmp3\b)")
_MP3 = re.compile(r"(?i)\b(?:mp3|aac|m4a|ogg|opus)\b")


# ── names ───────────────────────────────────────────────────────────────────


def _norm(text: str) -> str:
    t = (text or "").lower().replace("&", " and ").replace("'", "").replace("’", "")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"^the\s+", "", t.strip())


def _has(hay: str, needle: str) -> bool:
    return bool(needle) and f" {needle} " in f" {hay} "


def quality_of(title: str) -> tuple[int, str] | None:
    """(score, label) for a release name; None when it's a low-bitrate copy."""
    if _LOSSLESS.search(title):
        return 3, "flac"
    if _HIGH.search(title):
        return 2, "mp3-320"
    if _LOW.search(title):
        return None
    if _MP3.search(title):
        return 1, "mp3"
    return 1, "unknown"


_LEAD = re.compile(r"^\s*(?:(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\})\s*|(?:va|various artists?)\s*[-–:]\s*)+", re.I)


def _lead_stripped(title: str) -> str:
    """The release name without leading tags: "[Power Pop Indie Rock] Sloan …" → "Sloan …"."""
    return _LEAD.sub("", title or "")


def matches_album(title: str, artist: str, album: str) -> bool:
    """Is this release this artist's album (and not a video, or a whole discography)?
    The name must start with the artist, and the album must come after it; a short or
    numeric album title ("12", "IV") must come straight after the artist."""
    if _VIDEO.search(title):
        return False
    if _BUNDLE.search(title) and not _BUNDLE.search(album):
        return False
    t = _norm(_lead_stripped(title))
    a, b = _norm(artist), _norm(album)
    if not a or not b or not (t == a or t.startswith(a + " ")):
        return False
    rest = t[len(a):].strip()
    if len(b.replace(" ", "")) <= 3 or b.replace(" ", "").isdigit():
        return rest == b or rest.startswith(b + " ")
    return _has(rest, b)


_VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".m4v", ".ts", ".flv", ".webm", ".mpg", ".mpeg", ".vob"}


def not_music(names) -> str | None:
    """Why a set of downloaded file names isn't an album (video and no audio), or None."""
    from .music_library import AUDIO_EXTS
    import os
    exts = [os.path.splitext(n)[1].lower() for n in names or []]
    if not exts or any(e in AUDIO_EXTS for e in exts):
        return None
    if any(e in _VIDEO_EXTS for e in exts):
        return "Not music: the download was a video"
    return None


def reject_download(db, dl, reason: str) -> None:
    """A wrong download (a video, not the album): remove it from qBittorrent with its
    files, remember it as failed so it's never grabbed again, and want the album again."""
    from ..models import MusicAlbum
    from .qbittorrent import remove_torrent
    if dl.torrent_hash and not dl.qbit_removed:
        try:
            remove_torrent(dl.torrent_hash, delete_files=True)
        except Exception as exc:
            logger.warning("Couldn't remove %s from qBittorrent: %s", dl.title, exc)
    dl.qbit_removed = True
    dl.status = "error"
    dl.error_detail = reason
    dl.notified_failed = True
    album = db.get(MusicAlbum, dl.music_album_id) if dl.music_album_id else None
    if album is not None and album.status in ("grabbed", "have"):
        album.status = "wanted"
        album.completed_at = None
    db.commit()
    logger.warning("Rejected album download %s: %s", dl.title, reason)


# ── dates ───────────────────────────────────────────────────────────────────


def _date(s: str | None) -> date | None:
    s = (s or "").strip()
    for fmt, n in (("%Y-%m-%d", 10), ("%Y-%m", 7), ("%Y", 4)):
        try:
            return datetime.strptime(s[:n], fmt).date()
        except ValueError:
            continue
    return None


def _today() -> date:
    return datetime.now(timezone.utc).date()


def released(album, today: date | None = None) -> bool:
    d = _date(album.release_date)
    return d is None or d <= (today or _today())


def _hours_out(album) -> float | None:
    d = _date(album.release_date)
    if d is None:
        return None
    start = datetime.combine(d, datetime.min.time(), timezone.utc)
    return (datetime.now(timezone.utc) - start).total_seconds() / 3600


# ── adding and refreshing ───────────────────────────────────────────────────


class AlreadyFollowing(Exception):
    pass


def _initial_status(albums: list[dict], start: str) -> dict[str, str]:
    today = _today()
    out_now = [a for a in albums if (_date(a["date"]) or today) <= today]
    latest = max(out_now, key=lambda a: a["date"] or "", default=None)
    status = {}
    for a in albums:
        is_out = (_date(a["date"]) or today) <= today
        if start == "none" and is_out:
            status[a["mbid"]] = "ignored"
        elif start == "all" or not is_out or (start == "latest" and latest and a["mbid"] == latest["mbid"]):
            status[a["mbid"]] = "wanted"
        else:
            status[a["mbid"]] = "ignored"
    return status


def add_artist(db, mbid: str, start: str = "new", include_eps: bool = False,
               download_path_id: int | None = None):
    """Follow an artist. Raises AlreadyFollowing or musicbrainz.MusicBrainzUnavailable."""
    from ..models import MusicAlbum, WatchlistItem
    from . import musicbrainz

    if start not in STARTS:
        raise ValueError(f"start must be one of {', '.join(STARTS)}")
    if db.query(WatchlistItem).filter(WatchlistItem.kind == "artist", WatchlistItem.mbid == mbid).first():
        raise AlreadyFollowing()
    info = musicbrainz.artist(mbid)
    groups = musicbrainz.albums(mbid, include_eps)
    status = _initial_status(groups, start)
    item = WatchlistItem(kind="artist", mbid=mbid, title=info["name"], search_query=info["name"],
                         quality="flac", codec="any", season=1, episode=1, enabled=True,
                         summary=info["disambiguation"] or None, network=info["country"] or None,
                         prefer_season_packs=include_eps, download_path_id=download_path_id,
                         metadata_checked_at=datetime.now(timezone.utc))
    newest = next((g for g in reversed(groups) if g["date"]), None)
    if newest:
        item.poster_url = musicbrainz.cover_url(newest["mbid"])
    db.add(item)
    db.flush()
    for g in groups:
        row = MusicAlbum(watchlist_id=item.id, mbid=g["mbid"], title=g["title"], album_type=g["type"],
                         release_date=g["date"] or None, status=status[g["mbid"]])
        db.add(row)
        _mark_owned(db, item, row)
    db.commit()
    db.refresh(item)
    return item


# ── the music library: never download what's already there ──────────────────

_EDITION = re.compile(r"(?i)\s*[\(\[][^)\]]*(?:deluxe|remaster(?:ed)?|expanded|anniversary|edition|bonus|"
                      r"special|reissue|mono|stereo)[^)\]]*[\)\]]")


def _key(text: str | None) -> str:
    from .music_library import _norm
    return _norm(_EDITION.sub("", text or ""))


def library_copy(db, item, album):
    """The library album for this Watchlist album: same MusicBrainz release group, or the
    same artist and title (ignoring "(Deluxe)", "(Remastered)" and the like)."""
    from ..models import MusicLibAlbum
    if album.mbid:
        hit = db.query(MusicLibAlbum).filter(MusicLibAlbum.mb_release_group_id == album.mbid).first()
        if hit is not None:
            return hit
    artist, title = _key(item.title), _key(album.title)
    if not (artist and title):
        return None
    from .music_library import _norm
    for a in db.query(MusicLibAlbum).filter(MusicLibAlbum.title.isnot(None)):
        if _key(a.title) == title and _norm(a.artist) == _norm(item.title):
            return a
    return None


def is_upgrade(db, lib_album) -> bool:
    """Is the library copy worth replacing: lossy, low bitrate or missing tracks."""
    import json
    from ..models import MusicTrack
    flags = set(json.loads(lib_album.issues or "[]"))
    if flags & {"low_bitrate", "missing_tracks"}:
        return True
    tracks = db.query(MusicTrack).filter(MusicTrack.album_id == lib_album.id).all()
    return bool(tracks) and not all(t.lossless for t in tracks)


def _describe(db, lib_album) -> str:
    return f"{(lib_album.format or '?').upper()}, {lib_album.tracks} track{'s' if lib_album.tracks != 1 else ''}"


def _mark_owned(db, item, album) -> bool:
    """A Watchlist album already in the library (good enough) is "have", so it never
    shows as wanted. True if it was marked."""
    if album.status not in ("wanted", "ignored"):
        return False
    lib = library_copy(db, item, album)
    if lib is None or is_upgrade(db, lib):
        return False
    album.status = "have"
    album.quality = "flac" if (lib.format or "") == "flac" else (lib.format or None)
    return True


def refresh_albums(db, item, force: bool = False) -> int:
    """Add albums MusicBrainz lists that we haven't seen (as wanted); update dates.
    Daily at most unless forced. Returns how many were new. Never raises."""
    from ..models import MusicAlbum
    from . import musicbrainz

    last = item.metadata_checked_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if not force and last and datetime.now(timezone.utc) - last < REFRESH_EVERY:
        return 0
    try:
        groups = musicbrainz.albums(item.mbid, bool(item.prefer_season_packs))
    except Exception as exc:
        logger.info("Album refresh for %s skipped: %s", item.title, exc)
        return 0
    have = {a.mbid: a for a in db.query(MusicAlbum).filter(MusicAlbum.watchlist_id == item.id)}
    new = 0
    for g in groups:
        row = have.get(g["mbid"])
        if row is None:
            row = MusicAlbum(watchlist_id=item.id, mbid=g["mbid"], title=g["title"], album_type=g["type"],
                             release_date=g["date"] or None, status="wanted")
            db.add(row)
            _mark_owned(db, item, row)
            new += 1
        elif g["date"] and g["date"] != row.release_date:
            row.release_date = g["date"]
    item.metadata_checked_at = datetime.now(timezone.utc)
    newest = next((g for g in reversed(groups) if g["date"]), None)
    if newest:
        item.poster_url = musicbrainz.cover_url(newest["mbid"])
    db.commit()
    if new:
        logger.info("%s: %d new album(s)", item.title, new)
    return new


# ── state for the Watchlist card ────────────────────────────────────────────


def _active_download(db, album):
    from ..models import Download
    return (db.query(Download)
            .filter(Download.music_album_id == album.id, Download.status.notin_(["failed", "error"]))
            .order_by(Download.id.desc()).first())


def last_download(db, album):
    from ..models import Download
    return (db.query(Download).filter(Download.music_album_id == album.id)
            .order_by(Download.id.desc()).first())


def problem(db, album) -> str | None:
    """Why automatic downloads of this album have stopped, or None.

    A save-folder problem (qBittorrent can't write there) stops them at once, since
    every other copy would fail the same way and pile up in qBittorrent; anything
    else stops them after two failed downloads. Pressing download (force) tries again."""
    from ..models import Download
    from .qbittorrent import is_folder_problem
    last = last_download(db, album)
    if last is None or last.status not in ("failed", "error"):
        return None
    if is_folder_problem(last.error_detail):
        d = last.error_detail.rstrip(".")
        return d if d.lower().startswith("qbittorrent") else f"qBittorrent couldn't save it: {d}"
    failed = (db.query(Download).filter(Download.music_album_id == album.id,
                                        Download.status.in_(["failed", "error"])).count())
    if failed >= 2:
        return f"{failed} downloads failed" + (f" (last: {last.error_detail})" if last.error_detail else "")
    return None


def settle(db, albums) -> int:
    """Albums marked downloading whose download failed or was removed go back to
    wanted straight away (not only at the next scan). Returns how many changed."""
    n = 0
    for a in albums:
        if a.status == "grabbed" and _active_download(db, a) is None:
            a.status = "wanted"
            n += 1
    if n:
        db.commit()
    return n


def summary(db, item) -> dict:
    from ..models import MusicAlbum
    rows = db.query(MusicAlbum).filter(MusicAlbum.watchlist_id == item.id).all()
    settle(db, rows)
    counts = {k: 0 for k in ("wanted", "grabbed", "have", "ignored")}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    today = _today()
    upcoming = sorted((r for r in rows if r.status == "wanted" and not released(r, today)),
                      key=lambda r: r.release_date or "")
    nxt = upcoming[0] if upcoming else None
    return {**counts, "total": len(rows),
            "next": {"title": nxt.title, "date": nxt.release_date} if nxt else None}


# ── searching and grabbing ──────────────────────────────────────────────────


def find_candidates(item, album, min_seeds: int, exclude_hashes=frozenset(), exclude_titles=frozenset(),
                    lossless_only: bool = False) -> list[dict]:
    """Acceptable releases of this album, best first (quality, then seeds)."""
    from .indexers import search_all

    results = search_all(f"{item.search_query} {album.title}", quality=None, codec="any", filter_adult=True)
    out = []
    for r in results:
        if r.get("seeds", 0) < min_seeds or not matches_album(r["title"], item.search_query, album.title):
            continue
        if result_hash(r) in exclude_hashes or r["title"].lower() in exclude_titles:
            continue
        if item.max_size_mb and r.get("size_bytes", 0) > item.max_size_mb * 1024 * 1024:
            continue
        q = quality_of(r["title"])
        if q is None or (lossless_only and q[1] != "flac"):
            continue
        out.append({**r, "quality": q[1], "_score": q[0]})
    return sorted(out, key=lambda r: (r["_score"], r.get("seeds", 0)), reverse=True)


def music_folder(db, item):
    """Where album downloads go: the artist's folder, the Music folder setting, a folder
    named like music, else the default download folder."""
    from ..models import DownloadPath, Setting
    from .folders import download_folder
    from . import folder_roles
    if item.download_path_id:
        return download_folder(db, item.download_path_id)
    flagged = folder_roles.music_download_folder(db)
    if flagged is not None:
        return flagged
    row = db.query(Setting).filter(Setting.key == "music_download_path_id").first()
    if row and (row.value or "").isdigit():
        dp = db.get(DownloadPath, int(row.value))
        if dp is not None and dp.downloads:
            return dp
    for dp in db.query(DownloadPath).filter(DownloadPath.downloads.is_(True)).all():
        if "music" in (dp.name or "").lower() or "music" in (dp.path or "").lower():
            return dp
    return download_folder(db, None)


def grab(db, item, album, settings: dict | None = None, *, force: bool = False) -> dict:
    """Look for an album and start downloading the best copy.
    Returns {"status": waiting | already | not_found | duplicate | started, "message"}."""
    from ..models import Download, DownloadHistory, Setting
    from .apprise_notify import notify
    from .duplicate import check_duplicate
    from .qbittorrent import AddFailed, add_torrent, confirm_added, is_folder_problem, remove_torrent

    label = f"{item.title} – {album.title}"
    if album.status == "have" or _active_download(db, album) is not None:
        return {"status": "already", "message": f"{label} is already downloaded or downloading."}
    if not force and not released(album):
        return {"status": "waiting", "message": f"{label} isn't out yet (expected {album.release_date})."}
    lib = library_copy(db, item, album)
    upgrade_of = None
    if lib is not None:
        if not is_upgrade(db, lib):
            if not force:
                album.status = "have"
                album.quality = "flac" if (lib.format or "") == "flac" else (lib.format or None)
                db.commit()
                return {"status": "in_library", "message": f"{label} is already in your library ({_describe(db, lib)})."}
        else:
            upgrade_of = lib
    stop = None if force else problem(db, album)
    if stop:
        return {"status": "blocked", "message": f"{label}: not trying again by itself. {stop}. "
                                                "Fix it, then press download."}
    settings = settings if settings is not None else {s.key: s.value for s in db.query(Setting).all()}
    try:
        min_seeds = int(settings.get("min_seeds", "3"))
    except ValueError:
        min_seeds = 3
    age = _hours_out(album)
    lossless_only = (not force and age is not None and age < FALLBACK_AFTER_HOURS) or upgrade_of is not None

    with WATCHLIST_LOCK:
        cands = find_candidates(item, album, min_seeds, failed_hashes(db, item.id), failed_titles(db, item.id),
                                lossless_only)
        if not cands:
            why = (" in FLAC to replace your " + _describe(db, upgrade_of) + " copy" if upgrade_of is not None
                   else " in FLAC yet" if lossless_only else "")
            return {"status": "not_found", "message": f"No suitable copy of {label}{why}."}
        best = next((c for c in cands[:_TRY_TOP]
                     if not check_duplicate(torrent_hash=result_hash(c), torrent_name=c["title"], db=db)["is_duplicate"]),
                    None)
        if best is None:
            return {"status": "duplicate", "message": f"{label} looks like it was already downloaded."}
        dp = music_folder(db, item)
        save_path = dp.path if dp else "/downloads"
        info_hash = None
        try:
            info_hash = add_torrent(best["magnet"], save_path, settings.get("qbit_music_category") or "music")
            confirm_added(info_hash, best["title"])
            from .qbittorrent import file_names
            why = not_music(file_names(info_hash))
            if why:
                raise AddFailed(why)
        except AddFailed as exc:
            reason = str(exc)
            if info_hash:
                try:
                    remove_torrent(info_hash, delete_files=True)    # nothing was written; don't leave it errored
                except Exception:
                    pass
            db.add(Download(title=best["title"], torrent_hash=info_hash, magnet_link=best["magnet"],
                            size_bytes=best.get("size_bytes"), status="error", download_path=save_path,
                            watchlist_id=item.id, music_album_id=album.id, error_detail=reason[:400],
                            qbit_removed=True, notified_failed=True))
            db.commit()
            logger.warning("Album download refused for %s: %s", label, reason)
            hint = (f" qBittorrent needs write access to {save_path} (Settings → Folders → Test shows how)."
                    if is_folder_problem(reason) else "")
            return {"status": "error", "message": f"{label}: qBittorrent couldn't start it. {reason}.{hint}"}
        db.add(Download(title=best["title"], torrent_hash=info_hash, magnet_link=best["magnet"],
                        size_bytes=best.get("size_bytes"), status="downloading", download_path=save_path,
                        watchlist_id=item.id, music_album_id=album.id,
                        replaces_album_id=upgrade_of.id if upgrade_of is not None else None))
        db.add(DownloadHistory(name=best["title"], source="watchlist", indexer=best.get("source"),
                               folder=dp.name if dp else None, torrent_hash=info_hash,
                               size_bytes=best.get("size_bytes"), status="downloading", watchlist_id=item.id))
        album.status = "grabbed"
        album.torrent_name = best["title"]
        album.quality = best["quality"]
        album.grabbed_at = datetime.now(timezone.utc)
        item.last_found = album.grabbed_at
        db.commit()

    if settings.get("notify_on_grab", "true").strip().lower() == "true":
        notify(settings.get("apprise_url", ""), title=f"MediaMate — {item.title}",
               body=f"🎵 Downloading: {best['title']}")
    logger.info("Album grabbed: %s", best["title"])
    return {"status": "started", "title": best["title"], "message": f"Started: {best['title']}"}


def scan_artist(db, item, settings: dict) -> list[dict]:
    """One artist's turn in the watchlist scan."""
    from ..models import MusicAlbum
    refresh_albums(db, item)
    results = []
    albums = (db.query(MusicAlbum).filter(MusicAlbum.watchlist_id == item.id,
                                          MusicAlbum.status.in_(["wanted", "grabbed"]))
              .order_by(MusicAlbum.release_date.desc()).all())
    for album in albums:
        if album.status == "grabbed":
            if _active_download(db, album) is None:      # the download failed: look again
                album.status = "wanted"
                db.commit()
            else:
                continue
        if not released(album):
            continue
        r = grab(db, item, album, settings)
        results.append(r)
        if r["status"] == "started" and sum(x["status"] == "started" for x in results) >= MAX_GRABS_PER_SCAN:
            break
    return results


def on_download_complete(bind, download_id: int) -> bool:
    """A download finished: if it was an album, mark it "have". True if it was."""
    from sqlalchemy.orm import Session
    from ..models import Download, MusicAlbum

    with Session(bind=bind) as db:
        dl = db.get(Download, download_id)
        if dl is None or not dl.music_album_id:
            return False
        album = db.get(MusicAlbum, dl.music_album_id)
        if album is None or album.status == "have":
            return False
        album.status = "have"
        album.completed_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("Album finished: %s", album.title)
        return True
