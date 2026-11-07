"""Genre folders for movies: Movies/<Genre>/Title (Year)/Title (Year).ext.

Replaces the nightly organize_movies.py: the genre folders already in a movies library
are the choices (MediaMate never invents one), TMDB says which genres a movie has, and
the closest folder wins:

* exact       a combo folder ("Action & Adventure") whose genres are all the movie's
* single      the folder for the movie's first genre
* borderline  the folder sharing most genres with the movie (placed, but reported)
* none        TMDB found nothing, or no folder fits: <root>/_Unsorted

Folders on the skip list (Holiday, _Extras, _Unsorted by default) are never chosen and
what's inside them is never moved. A movie already inside a genre folder stays there.
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

UNSORTED = "_Unsorted"
DEFAULT_SKIP = "Holiday, _Extras, _Unsorted"
RETRY = timedelta(days=30)

# TMDB movie genres (ids are fixed).
TMDB = {28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 80: "Crime", 99: "Documentary",
        18: "Drama", 10751: "Family", 14: "Fantasy", 36: "History", 27: "Horror", 10402: "Music",
        9648: "Mystery", 10749: "Romance", 878: "Science Fiction", 10770: "TV Movie", 53: "Thriller",
        10752: "War", 37: "Western"}

# Words a folder name may use for a genre.
ALIASES = {
    "action": "Action", "adventure": "Adventure", "adventures": "Adventure",
    "animation": "Animation", "animated": "Animation", "anime": "Animation", "cartoon": "Animation",
    "cartoons": "Animation", "comedy": "Comedy", "comedies": "Comedy", "crime": "Crime",
    "documentary": "Documentary", "documentaries": "Documentary", "docs": "Documentary", "doc": "Documentary",
    "drama": "Drama", "dramas": "Drama", "family": "Family", "kids": "Family", "children": "Family",
    "fantasy": "Fantasy", "history": "History", "historical": "History", "horror": "Horror",
    "music": "Music", "musical": "Music", "musicals": "Music", "mystery": "Mystery", "mysteries": "Mystery",
    "romance": "Romance", "romantic": "Romance", "romcom": "Romance", "science fiction": "Science Fiction",
    "sci fi": "Science Fiction", "scifi": "Science Fiction", "sf": "Science Fiction",
    "thriller": "Thriller", "thrillers": "Thriller", "suspense": "Thriller", "war": "War",
    "western": "Western", "westerns": "Western", "tv movie": "TV Movie",
}
_TITLE_YEAR = re.compile(r"\((?:19|20)\d\d\)\s*$")
_SPLIT = re.compile(r"\s*(?:&|\+|,|/|\band\b|\s-\s)\s*", re.I)


def skip_list(settings: dict) -> set[str]:
    raw = settings.get("organise_movie_genre_skip")
    raw = DEFAULT_SKIP if raw is None else raw
    return {s.strip().lower() for s in raw.split(",") if s.strip()} | {UNSORTED.lower()}


def enabled(settings: dict) -> bool:
    return str(settings.get("organise_movie_genres", "")).lower() == "true"


def folder_genres(name: str) -> frozenset[str]:
    """The TMDB genres a folder name stands for ("Sci-Fi & Fantasy" → both)."""
    text = re.sub(r"[^a-z0-9&+,/ ]+", " ", name.lower().replace("-", " ")).strip()
    text = re.sub(r"\s+", " ", text)
    if text in ALIASES:
        return frozenset([ALIASES[text]])
    out = set()
    for part in _SPLIT.split(text):
        part = part.strip()
        if part in ALIASES:
            out.add(ALIASES[part])
        else:
            for w in part.split():
                if w in ALIASES:
                    out.add(ALIASES[w])
    return frozenset(out)


def genre_folders(root: str, settings: dict) -> dict[str, frozenset[str]]:
    """{folder name: genres} for the genre folders in a movies library. A folder that
    isn't a Title (Year) folder and isn't skipped counts, even when its name maps to no
    TMDB genre (movies already in it stay; it's just never chosen)."""
    skip = skip_list(settings)
    try:
        names = sorted(n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n)))
    except OSError:
        return {}
    out = {}
    for n in names:
        if n.startswith(".") or n.lower() in skip or _TITLE_YEAR.search(n):
            continue
        gs = folder_genres(n)
        if gs or _looks_like_a_shelf(os.path.join(root, n), n):
            out[n] = gs
    return out


def _looks_like_a_shelf(path: str, name: str) -> bool:
    """A folder with a non-genre name ("Superhero", "Marvel") still counts as a genre
    folder when it holds movie folders rather than being a movie itself: no year or
    release tags in its name and no video file directly inside it."""
    from .organise import _JUNK, _YEAR, _video_exts
    if _YEAR.search(name) or _JUNK.search(name):
        return False
    try:
        return not any(os.path.splitext(f)[1].lower() in _video_exts() for f in os.listdir(path)
                       if os.path.isfile(os.path.join(path, f)))
    except OSError:
        return False


def pick(genres: list[str], folders: dict[str, frozenset[str]]) -> tuple[str, str]:
    """(folder, confidence) for a movie with these TMDB genres (first = main)."""
    have = set(genres)
    if not have:
        return UNSORTED, "none"
    order = {g: i for i, g in enumerate(genres)}
    combos = [(n, gs) for n, gs in folders.items() if len(gs) >= 2 and gs <= have]
    if combos:
        n, _ = max(combos, key=lambda x: (len(x[1]), -min(order[g] for g in x[1])))
        return n, "exact"
    for n, gs in folders.items():
        if gs == {genres[0]}:
            return n, "single"
    overlap = [(n, gs) for n, gs in folders.items() if gs & have]
    if overlap:
        n, _ = max(overlap, key=lambda x: (len(x[1] & have), -min(order[g] for g in x[1] & have)))
        return n, "borderline"
    return UNSORTED, "none"


def where_now(root: str, path: str, settings: dict) -> tuple[str | None, bool]:
    """(genre folder the file is in, or None; whether it's in a skipped folder)."""
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    if rel.startswith(".."):
        return None, False
    first = rel.split(os.sep)[0]
    if os.sep not in rel:
        return None, False                       # loose at the root
    if first.lower() in skip_list(settings):
        return None, True
    return (first if first in genre_folders(root, settings) else None), False


def keep_shelf(root: str, path: str, settings: dict) -> str | None:
    """Genre folders off: a movie already on a shelf (a genre folder) keeps it, so nothing
    flattens a library sorted by hand or by the old nightly script."""
    from .organise import OrganiseError
    now, skipped = where_now(root, path, settings)
    if skipped:
        raise OrganiseError("It's in a folder Rename & organise leaves alone")
    return now


_EXTRA_WORDS = re.compile(
    r"(?i)\b(trailers?|teasers?|featurettes?|interviews?|behind[ ._-]the[ ._-]scenes|deleted[ ._-]scenes?|"
    r"photo[ ._-]gallery|making[ ._-]of|introduction|press[ ._-]kit|storyboard|bloopers?|gag[ ._-]reel|"
    r"post[ ._-]?sync|extras?|bonus|sample)\b")
_EXTRA_DIRS = {"extras", "featurettes", "behind the scenes", "deleted scenes", "trailers", "interviews",
               "shorts", "other", "scenes", "bonus", "_extras"}


def is_extra(root: str, path: str) -> bool:
    """Bonus material (trailers, interviews, featurettes…): never organised or unsorted."""
    rel = os.path.relpath(path, root).split(os.sep)
    if any(p.lower() in _EXTRA_DIRS for p in rel[:-1]):
        return True
    return bool(_EXTRA_WORDS.search(os.path.splitext(rel[-1])[0]))


async def _fetch(key: str, title: str, year: int | None) -> list[str] | None:
    from . import tmdb
    params = {"query": title, "include_adult": "false"}
    if year:
        params["year"] = str(year)
    found = (await tmdb._get(key, "/search/movie", params)).get("results") or []
    if not found and year:
        found = (await tmdb._get(key, "/search/movie", {"query": title})).get("results") or []
    if not found:
        return None
    return [TMDB[g] for g in found[0].get("genre_ids") or [] if g in TMDB]


def genres_for(db, title: str, year: int | None, lookup: bool = True) -> list[str] | None:
    """A movie's TMDB genres (main first), cached with the age ratings. [] = TMDB has
    nothing; None = not known yet (no key, not allowed to look up, or TMDB is down)."""
    import asyncio
    from ..models import ContentRating
    from . import tmdb
    from .ratings import _key
    k = _key(title)
    if not k:
        return []
    row = db.query(ContentRating).filter_by(kind="movie", title_key=k, year=year or 0).first()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if row is not None and row.genres is not None:
        got = json.loads(row.genres)
        if got or (row.genres_checked_at and now - row.genres_checked_at < RETRY):
            return got
    if not lookup:
        return None
    from .show_metadata import tmdb_key
    key = tmdb_key(db)
    if not key:
        return None
    try:
        got = asyncio.run(_fetch(key, title, year))
    except tmdb.TMDBUnavailable as exc:
        logger.info("Genre lookup for %s unavailable: %s", title, exc)
        return None
    except Exception:
        logger.exception("Genre lookup for %s failed", title)
        return None
    if row is None:
        row = ContentRating(kind="movie", title_key=k, year=year or 0, title=title)
        db.add(row)
    row.genres, row.genres_checked_at = json.dumps(got or []), now
    db.commit()
    return got or []


def choose(db, root: str, path: str, title: str, year: int | None, settings: dict,
           lookup: bool = True) -> tuple[str | None, str]:
    """(genre folder, confidence) for a movie file. A movie already in a genre folder
    keeps it ("kept"). Raises organise.OrganiseError for skipped folders and when the
    genres can't be known yet."""
    from .organise import OrganiseError
    now, skipped = where_now(root, path, settings)
    if skipped:
        raise OrganiseError("It's in a folder Rename & organise leaves alone "
                            "(Settings → Auto pipeline → Rename & organise → folders to leave alone)")
    if now:
        return now, "kept"
    genres = genres_for(db, title, year, lookup)
    if genres is None:
        raise OrganiseError("Genre folders need TMDB: add your TMDB key in Settings, or try again later")
    return pick(genres, genre_folders(root, settings))
