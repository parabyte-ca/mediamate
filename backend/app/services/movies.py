"""Movies on the watchlist: wait for the release, download once, then leave.

A movie item ("kind" = "movie") is searched for only once its release date (the
digital release from TMDB, else the disc release, else cinemas + 90 days) has come.
The best release is grabbed like an episode, but only once. When that download
finishes, ``completed_at`` is set and the item stops being scanned; some days later
(``watchlist_movie_keep_days``, default 30) it is removed from the watchlist unless
it's marked "keep". The file itself stays in the Library.
"""
import logging
import re
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from .candidates import WATCHLIST_LOCK, effective_item, failed_hashes, failed_titles, matches_movie, \
    query_words, result_hash

logger = logging.getLogger(__name__)

KEEP_DAYS_KEY = "watchlist_movie_keep_days"
DEFAULT_KEEP_DAYS = 30
FALLBACK_AFTER_HOURS = 48          # lower quality this long after release when nothing better turned up
_EPISODE = re.compile(r"(?i)\bS\d{1,2}E\d{1,3}\b|\b\d{1,2}x\d{2}\b")
_YEARS = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_TRY_TOP = 5


def _today() -> date:
    return datetime.now(timezone.utc).date()


def released(item, today: date | None = None) -> bool:
    """Has the movie's (digital) release come? Unknown date: yes, so it's still looked for."""
    if not item.release_date:
        return True
    try:
        return date.fromisoformat(item.release_date[:10]) <= (today or _today())
    except ValueError:
        return True


def _active_download(db, item):
    from ..models import Download
    return (db.query(Download)
            .filter(Download.watchlist_id == item.id, Download.status.notin_(["failed", "error"]))
            .order_by(Download.id.desc()).first())


def state(db, item) -> str:
    """waiting | looking | downloading | downloaded | paused, for the Watchlist card."""
    if item.completed_at:
        return "downloaded"
    dl = _active_download(db, item)
    if dl is not None:
        return "downloaded" if dl.status in ("completed", "seeding", "done") else "downloading"
    if not released(item):
        return "waiting"
    return "looking" if item.enabled else "paused"


def _year_ok(title: str, year: int | None) -> bool:
    if not year:
        return True
    years = [int(y) for y in _YEARS.findall(title)]
    # A release with no year at all is allowed; one naming another year is a different film.
    return not years or any(abs(y - year) <= 1 for y in years)


def find_candidates(eff, year: int | None, min_seeds: int, exclude_hashes=frozenset(),
                    exclude_titles=frozenset()) -> list[dict]:
    """Acceptable releases of the movie, best first (preferred group, then seeds)."""
    from .indexers import search_all

    query = f"{eff.search_query} {year}" if year else eff.search_query
    results = search_all(query, quality=eff.quality, codec=eff.codec or "x265", filter_adult=True)
    words = query_words(eff.search_query)
    results = [r for r in results
               if r.get("seeds", 0) >= min_seeds
               and not _EPISODE.search(r["title"])
               and matches_movie(r["title"], (eff.search_query, eff.title), year)
               and _year_ok(r["title"], year)]
    if eff.max_size_mb:
        results = [r for r in results if r.get("size_bytes", 0) <= eff.max_size_mb * 1024 * 1024]
    if exclude_hashes:
        results = [r for r in results if result_hash(r) not in exclude_hashes]
    if exclude_titles:
        results = [r for r in results if r["title"].lower() not in exclude_titles]
    preferred = [g.strip().lower() for g in (eff.preferred_groups or "").split(",") if g.strip()]

    def group_score(r: dict) -> int:
        t = r["title"].lower()
        return next((len(preferred) - i for i, g in enumerate(preferred) if g in t), 0)

    return sorted(results, key=lambda r: (group_score(r), r.get("seeds", 0)), reverse=True)


def _hours_since_release(item) -> float | None:
    if not item.release_date:
        return None
    try:
        start = datetime.combine(date.fromisoformat(item.release_date[:10]), datetime.min.time(), timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - start).total_seconds() / 3600


def find_with_fallback(db, item, min_seeds: int) -> list[dict]:
    eff = effective_item(db, item)
    bad_h, bad_t = failed_hashes(db, item.id), failed_titles(db, item.id)
    results = find_candidates(eff, item.year, min_seeds, bad_h, bad_t)
    if results or not eff.fallback_quality or eff.fallback_quality == eff.quality:
        return results
    age = _hours_since_release(item)
    if age is None or age < max(FALLBACK_AFTER_HOURS, eff.fallback_after_hours or 0):
        return results
    fallback = SimpleNamespace(**{**vars(eff), "quality": eff.fallback_quality})
    return find_candidates(fallback, item.year, min_seeds, bad_h, bad_t)


def grab(db, item, settings: dict | None = None, *, force: bool = False) -> dict:
    """Look for the movie and start downloading the best release.

    Returns {"status": waiting | already | not_found | duplicate | started, "message"}.
    ``force`` searches even before the release date (the "Search now" button).
    Raises on a qBittorrent failure so the caller can report it.
    """
    from ..models import Download, DownloadHistory, Setting
    from .apprise_notify import notify
    from .duplicate import check_duplicate
    from .qbittorrent import add_torrent

    if item.completed_at or _active_download(db, item) is not None:
        return {"status": "already", "message": f"{item.title} is already downloaded or downloading."}
    if not force and not released(item):
        return {"status": "waiting", "message": f"{item.title} isn't out yet (expected {item.release_date})."}
    settings = settings if settings is not None else {s.key: s.value for s in db.query(Setting).all()}
    try:
        min_seeds = int(settings.get("min_seeds", "3"))
    except ValueError:
        min_seeds = 3
    label = f"{item.title} ({item.year})" if item.year else item.title

    with WATCHLIST_LOCK:
        candidates = find_with_fallback(db, item, min_seeds)
        if not candidates:
            return {"status": "not_found", "message": f"No suitable release of {label} yet."}
        best = None
        for cand in candidates[:_TRY_TOP]:
            if not check_duplicate(torrent_hash=result_hash(cand), torrent_name=cand["title"], db=db)["is_duplicate"]:
                best = cand
                break
        if best is None:
            return {"status": "duplicate", "message": f"{label} looks like it was already downloaded."}
        from .folders import route_for
        dp, _why = route_for(db, item)
        save_path = dp.path if dp else "/downloads"
        info_hash = add_torrent(best["magnet"], save_path, settings.get("qbit_category", "mediamate"))
        db.add(Download(title=best["title"], torrent_hash=info_hash, magnet_link=best["magnet"],
                        size_bytes=best.get("size_bytes"), status="downloading",
                        download_path=save_path, watchlist_id=item.id))
        db.add(DownloadHistory(name=best["title"], source="watchlist", indexer=best.get("source"),
                               folder=dp.name if dp else None, torrent_hash=info_hash,
                               size_bytes=best.get("size_bytes"), status="downloading", watchlist_id=item.id))
        item.last_found = datetime.now(timezone.utc)
        db.commit()

    if settings.get("notify_on_grab", "true").strip().lower() == "true":
        notify(settings.get("apprise_url", ""), title=f"MediaMate — {item.title}",
               body=f"⬇️ Downloaded: {best['title']}", watchlist_id=item.id)
    logger.info("Movie grabbed: %s", best["title"])
    return {"status": "started", "title": best["title"], "message": f"Started: {best['title']}"}


def on_download_complete(bind, download_id: int) -> bool:
    """A download finished: if it was a watchlist movie, mark the movie done. True if it was."""
    from sqlalchemy.orm import Session
    from ..models import Download, WatchlistItem

    with Session(bind=bind) as db:
        dl = db.get(Download, download_id)
        if dl is None or not dl.watchlist_id:
            return False
        item = db.get(WatchlistItem, dl.watchlist_id)
        if item is None or item.kind != "movie" or item.completed_at:
            return False
        item.completed_at = datetime.now(timezone.utc)
        item.enabled = False
        db.commit()
        logger.info("Watchlist movie finished: %s", item.title)
        return True


def keep_days(db) -> int:
    from ..models import Setting
    row = db.query(Setting).filter(Setting.key == KEEP_DAYS_KEY).first()
    try:
        return max(0, int(row.value)) if row and row.value not in (None, "") else DEFAULT_KEEP_DAYS
    except ValueError:
        return DEFAULT_KEEP_DAYS


def removes_on(db, item, days: int | None = None) -> datetime | None:
    """When a finished movie leaves the watchlist (None: kept, not finished, or never)."""
    days = keep_days(db) if days is None else days
    if not item.completed_at or item.keep or days <= 0:
        return None
    done = item.completed_at if item.completed_at.tzinfo else item.completed_at.replace(tzinfo=timezone.utc)
    return done + timedelta(days=days)


def cleanup_finished(db, now: datetime | None = None) -> list[str]:
    """Remove finished movies older than the keep period. Returns their titles."""
    from ..models import WatchlistItem

    days = keep_days(db)
    if days <= 0:
        return []
    now = now or datetime.now(timezone.utc)
    gone = []
    for item in db.query(WatchlistItem).filter(WatchlistItem.kind == "movie",
                                               WatchlistItem.completed_at.isnot(None)).all():
        when = removes_on(db, item, days)
        if when is not None and when <= now:
            gone.append(item.title)
            db.delete(item)
    if gone:
        db.commit()
        logger.info("Removed %d finished movie(s) from the watchlist: %s", len(gone), ", ".join(gone))
    return gone
