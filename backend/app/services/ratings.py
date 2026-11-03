"""Age ratings and audience: does a show or movie go in the regular folders or the
Mature ones?

  1. Your choice wins: a watchlist show set to Regular or Mature.
  2. Otherwise the official rating from TMDB (US first, then Canada).
       Regular: G, PG, PG-13, TV-Y, TV-Y7, TV-G, TV-PG, TV-14; CA G, PG, 14A, C, C8, 14+
       Mature:  R, NC-17, TV-MA; CA 18A, R, A, 18+
  3. No rating found (or no TMDB key): Mature, so nothing reaches the kids by mistake.

Lookups are cached in content_ratings (also "not found", retried after a week).
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import ContentRating
from . import tmdb

logger = logging.getLogger(__name__)

GENERAL, MATURE = "general", "mature"
REGULAR = {
    "G", "PG", "PG-13", "TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG", "TV-14",   # US
    "14A", "C", "C8", "14+", "E",                                                 # Canada
}
ADULT = {"R", "NC-17", "TV-MA", "18A", "A", "18+", "X"}
COUNTRIES = ("US", "CA")
NOT_FOUND_RETRY = timedelta(days=7)


def classify(cert: str | None) -> str | None:
    """general / mature for a certification, or None when it's unknown ("NR", "")."""
    c = (cert or "").strip().upper()
    if c in REGULAR:
        return GENERAL
    if c in ADULT:
        return MATURE
    return None


def _key(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.lower())


def _pick(certs: dict[str, str]) -> str | None:
    for country in COUNTRIES:
        if classify(certs.get(country)):
            return certs[country]
    return None


async def fetch(key: str, kind: str, title: str, year: int | None = None) -> str | None:
    """The best US/CA certification TMDB has, or None. Raises tmdb.TMDBUnavailable."""
    if kind == "movie":
        params = {"query": title, "include_adult": "false"}
        if year:
            params["year"] = str(year)
        found = (await tmdb._get(key, "/search/movie", params)).get("results") or []
        if not found and year:
            found = (await tmdb._get(key, "/search/movie", {"query": title})).get("results") or []
        if not found:
            return None
        data = await tmdb._get(key, f"/movie/{int(found[0]['id'])}/release_dates")
        certs = {}
        for r in data.get("results") or []:
            for d in r.get("release_dates") or []:
                if d.get("certification"):
                    certs.setdefault(r.get("iso_3166_1"), d["certification"])
        return _pick(certs)
    params = {"query": title}
    if year:
        params["first_air_date_year"] = str(year)
    found = (await tmdb._get(key, "/search/tv", params)).get("results") or []
    if not found:
        return None
    data = await tmdb._get(key, f"/tv/{int(found[0]['id'])}/content_ratings")
    return _pick({r.get("iso_3166_1"): r.get("rating") for r in data.get("results") or [] if r.get("rating")})


def certification(db: Session, kind: str, title: str, year: int | None = None,
                  lookup: bool = True) -> str | None:
    """Cached certification for a title, looking it up on TMDB when needed and allowed."""
    k = _key(title)
    if not k:
        return None
    row = db.query(ContentRating).filter_by(kind=kind, title_key=k, year=year or 0).first()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if row and (row.cert or (row.checked_at and now - row.checked_at < NOT_FOUND_RETRY)):
        return row.cert or None
    if not lookup:
        return None
    from .show_metadata import tmdb_key
    key = tmdb_key(db)
    if not key:
        return None
    try:
        cert = asyncio.run(fetch(key, kind, title, year))
    except tmdb.TMDBUnavailable as exc:
        logger.info("Rating lookup for %s unavailable: %s", title, exc)
        return None                              # not cached: try again next time
    except Exception:
        logger.exception("Rating lookup for %s failed", title)
        return None
    if row is None:
        row = ContentRating(kind=kind, title_key=k, year=year or 0, title=title)
        db.add(row)
    row.cert, row.checked_at = cert or "", now
    db.commit()
    return cert


def audience(db: Session, kind: str | None, title: str, year: int | None = None,
             watchlist_item=None, lookup: bool = True) -> tuple[str, str]:
    """(general|mature, reason) for a show or movie."""
    choice = getattr(watchlist_item, "audience", None) if watchlist_item is not None else None
    if choice in (GENERAL, MATURE):
        return choice, "set on the Watchlist"
    if kind not in ("episode", "movie"):
        return MATURE, "not sure what this is, so Mature to be safe"
    cert = certification(db, "tv" if kind == "episode" else "movie", title, year, lookup=lookup)
    level = classify(cert)
    if level:
        return level, f"rated {cert}"
    return MATURE, "no rating found, so Mature to be safe"
