import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Setting, WatchlistItem
from ..services import browse as browse_service

router = APIRouter()


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


@router.get("/browse")
async def browse(q: str = "", db: Session = Depends(get_db)):
    """Shows airing soon (default) or a TVMaze search, flagged if already on the watchlist."""
    row = db.query(Setting).filter(Setting.key == "browse_country").first()
    country = browse_service.valid_country(row.value if row else None)

    query = q.strip()
    if query:
        if len(query) < 2:
            return {"mode": "search", "country": country, "shows": []}
        shows = await browse_service.search(query)
        mode = "search"
    else:
        shows = await browse_service.airing_shows(country)
        mode = "airing"
    if shows is None:
        raise HTTPException(status_code=502, detail="Could not reach TVMaze. Try again in a moment.")

    items = db.query(WatchlistItem).all()
    by_id = {i.tvmaze_id: i.id for i in items if i.tvmaze_id}
    by_name = {_norm(i.title): i.id for i in items}
    for show in shows:
        wid = by_id.get(show["tvmaze_id"]) or by_name.get(_norm(show["name"]))
        show["watchlist_id"] = wid
        show["on_watchlist"] = wid is not None
    return {"mode": mode, "country": country, "shows": shows}


@router.get("/browse/movies")
async def browse_movies(q: str = "", list: str = "popular", db: Session = Depends(get_db)):
    """Popular / in theatres / coming soon movies, or a search (TMDB), flagged if already in the library."""
    from ..services import organise, ratings, tmdb
    from ..services.media import store
    from ..services.show_metadata import tmdb_key

    key = tmdb_key(db)
    if not key:
        return {"need_key": True, "movies": [], "list": list}
    row = db.query(Setting).filter(Setting.key == "browse_country").first()
    country = browse_service.valid_country(row.value if row else None)
    query = q.strip()
    if query and len(query) < 2:
        return {"need_key": False, "movies": [], "list": "search", "country": country}
    try:
        movies = await browse_service.movies(key, list, query, country)
    except tmdb.TMDBUnavailable as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach TMDB ({exc}). Try again in a moment.")

    # Already in the library? Match by title (and year when both have one).
    have = {}
    for r in store.get_all_files():
        p = organise.parse(r["path"])
        if p.kind == "movie":
            have.setdefault(ratings._key(p.title), set()).add(p.year)
    for m in movies:
        years = have.get(ratings._key(m["title"]))
        m["in_library"] = bool(years) and (m["year"] is None or m["year"] in years or None in years)
        # Age rating when we already know it (no extra lookups for a whole list).
        m["certification"] = ratings.certification(db, "movie", m["title"], m["year"], lookup=False)
    return {"need_key": False, "movies": movies, "list": "search" if query else list, "country": country}
