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
    on_list = {w.tmdb_id: w.id for w in db.query(WatchlistItem).filter(WatchlistItem.tmdb_id.isnot(None))}
    for m in movies:
        m["watchlist_id"] = on_list.get(m["tmdb_id"])
        m["on_watchlist"] = m["watchlist_id"] is not None
        years = have.get(ratings._key(m["title"]))
        m["in_library"] = bool(years) and (m["year"] is None or m["year"] in years or None in years)
        # Age rating when we already know it (no extra lookups for a whole list).
        m["certification"] = ratings.certification(db, "movie", m["title"], m["year"], lookup=False)
    return {"need_key": False, "movies": movies, "list": "search" if query else list, "country": country}


# ── music ───────────────────────────────────────────────────────────────────


@router.get("/browse/music")
def browse_music(list: str = "new", for_you: bool = True, db: Session = Depends(get_db)):
    """New releases or popular albums (ListenBrainz), ranked for you."""
    from ..services import music_discover
    if list not in ("new", "popular"):
        raise HTTPException(status_code=400, detail="list must be new or popular")
    try:
        rows = music_discover.releases(list, db.get_bind())
    except music_discover.Unavailable as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach ListenBrainz ({exc}). Try again in a moment.")
    out = music_discover.rank(db, rows, for_you)
    out["ratings_pending"] = music_discover.start_ratings(db, out["releases"], rows)
    if not out["taste_ready"]:
        music_discover.start_taste(db.get_bind())
    return {"list": list, "for_you": for_you, **out}


@router.post("/browse/music/taste")
def rebuild_taste(db: Session = Depends(get_db)):
    """Look up your artists' genres again (in the background)."""
    from ..services import music_discover
    return {"started": music_discover.start_taste(db.get_bind())}


@router.post("/browse/music/add")
def browse_music_add(body: dict, db: Session = Depends(get_db)):
    """Follow the artist (future releases) and want this album."""
    from ..services import music, music_discover, musicbrainz
    artist, rg = str(body.get("artist_mbid") or ""), str(body.get("mbid") or "")
    if not artist or not rg:
        raise HTTPException(status_code=400, detail="artist_mbid and mbid are needed")
    try:
        return music_discover.add(db, artist, rg, str(body.get("title") or ""), body.get("date"), body.get("type"))
    except music.AlreadyFollowing:
        raise HTTPException(status_code=409, detail="Already following")
    except musicbrainz.MusicBrainzUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"MusicBrainz is unavailable right now ({exc})")
