"""Create a watchlist entry from a TVMaze show (Browse "Add" and Trakt sync)."""
from datetime import datetime, timezone

from . import episode_guide, show_metadata, tvmaze


class ShowNotFound(Exception):
    """TVMaze does not know the requested show."""


class AlreadyOnWatchlist(Exception):
    """A watchlist entry for this TVMaze show already exists."""


def default_profile_id(db) -> int | None:
    """The configured default quality profile, ignoring one that was deleted."""
    from ..models import QualityProfile, Setting

    row = db.query(Setting).filter(Setting.key == "default_profile_id").first()
    if row and row.value and row.value.isdigit():
        if db.query(QualityProfile).filter(QualityProfile.id == int(row.value)).first():
            return int(row.value)
    return None


async def create_from_tvmaze(db, tvmaze_id: int, start: str | None = None,
                             profile_id: int | None = None, show: dict | None = None):
    """Add a show, starting at its next new episode or the beginning.

    ``start`` is "next", "beginning" or None (None: next for running shows,
    beginning otherwise).  ``profile_id`` None means the default profile.  Pass
    ``show`` if the TVMaze record was already fetched.  Raises
    :class:`AlreadyOnWatchlist` / :class:`ShowNotFound`.
    """
    from ..models import WatchlistItem

    if db.query(WatchlistItem).filter(WatchlistItem.tvmaze_id == tvmaze_id).first():
        raise AlreadyOnWatchlist()
    if show is None:
        show = await tvmaze.get_show(tvmaze_id)
    if show is None:
        raise ShowNotFound()

    if profile_id is None:
        profile_id = default_profile_id(db)

    episodes = await episode_guide.aget_episodes(tvmaze_id)
    season, episode = show_metadata.starting_point(episodes, show.get("status"), start)
    name = show.get("name") or "Unknown show"
    item = WatchlistItem(
        title=name,
        search_query=show_metadata.search_query_from_name(name),
        season=season,
        episode=episode,
        profile_id=profile_id,
    )
    show_metadata.apply_show(item, show)
    item.metadata_checked_at = datetime.now(timezone.utc)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


class NoTmdbKey(Exception):
    """Movies need the user's TMDB key."""


def _country(db) -> str:
    from ..models import Setting
    from .browse import valid_country
    row = db.query(Setting).filter(Setting.key == "browse_country").first()
    return valid_country(row.value if row else None)


async def create_from_tmdb(db, tmdb_id: int, profile_id: int | None = None, audience: str | None = None):
    """Add a movie: it waits for its digital release, then downloads once.

    Raises :class:`NoTmdbKey`, :class:`AlreadyOnWatchlist`, or tmdb.TMDBUnavailable.
    """
    from ..models import WatchlistItem
    from . import tmdb

    key = show_metadata.tmdb_key(db)
    if not key:
        raise NoTmdbKey()
    if db.query(WatchlistItem).filter(WatchlistItem.tmdb_id == tmdb_id).first():
        raise AlreadyOnWatchlist()
    movie = await tmdb.movie(key, tmdb_id)
    released = (movie.get("release_date") or "")[:10]
    release_date, release_kind = await tmdb.release_info(key, tmdb_id, _country(db), released)
    title = movie.get("title") or movie.get("original_title") or "Unknown movie"
    item = WatchlistItem(
        kind="movie",
        tmdb_id=tmdb_id,
        title=title,
        search_query=show_metadata.search_query_from_name(title),
        year=int(released[:4]) if released[:4].isdigit() else None,
        release_date=release_date,
        release_kind=release_kind,
        season=1,
        episode=1,
        profile_id=profile_id if profile_id is not None else default_profile_id(db),
        audience=None if audience in (None, "auto") else audience,
    )
    show_metadata.apply_movie(item, movie)
    item.metadata_checked_at = datetime.now(timezone.utc)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


async def refresh_release(db, item) -> bool:
    """Re-read a movie's release date from TMDB (dates move). True if it changed. Never raises."""
    from . import tmdb
    key = show_metadata.tmdb_key(db)
    if item.kind != "movie" or not item.tmdb_id or not key:
        return False
    try:
        fallback = f"{item.year}-01-01" if item.year and not item.release_date else None
        date, kind = await tmdb.release_info(key, item.tmdb_id, _country(db), fallback)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).info("Release date refresh for %r skipped: %s", item.title, exc)
        return False
    if date and (date, kind) != (item.release_date, item.release_kind):
        item.release_date, item.release_kind = date, kind
        db.commit()
        return True
    return False
