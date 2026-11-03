import asyncio
import logging
import re
import httpx

logger = logging.getLogger(__name__)
_BASE = "https://api.tvmaze.com"

_STATUS_MAP = {
    "Running": "Running",
    "Ended": "Ended",
    "To Be Determined": "To Be Determined",
    "In Development": "In Development",
}


class TVMazeUnavailable(Exception):
    """TVMaze could not answer (network error, rate limit, server error).

    Distinct from "no such show": callers must not remember this as a negative result.
    """


async def _get_json(client: httpx.AsyncClient, path: str, params: dict | None = None):
    """GET from TVMaze, politely retrying when rate limited (HTTP 429)."""
    for attempt in range(3):
        resp = await client.get(f"{_BASE}{path}", params=params)
        if resp.status_code == 429 and attempt < 2:
            try:
                delay = min(float(resp.headers.get("retry-after", "1")), 5.0)
            except ValueError:
                delay = 1.0
            await asyncio.sleep(delay)
            continue
        return resp
    return resp


async def _strict_get(path: str, params: dict | None = None, timeout: float = 10):
    """GET JSON from TVMaze. None means a genuine 404; raises TVMazeUnavailable on any failure."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await _get_json(client, path, params)
    except httpx.HTTPError as exc:
        raise TVMazeUnavailable(str(exc) or type(exc).__name__) from exc
    if resp.status_code == 404:
        return None
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TVMazeUnavailable(f"HTTP {resp.status_code}")
    try:
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TVMazeUnavailable(str(exc) or type(exc).__name__) from exc


_SUFFIX = re.compile(r"\s*\((?:US|UK|AU|CA|NZ|\d{4})\)\s*$", re.IGNORECASE)


def norm_name(name: str) -> str:
    """Compare-friendly name: "The Office (US)" -> "office", "Law & Order" -> "lawandorder"."""
    n = _SUFFIX.sub("", name or "").lower().replace("&", " and ")
    n = re.sub(r"^the\s+", "", n.strip())
    return re.sub(r"[^a-z0-9]", "", n)


def _words(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _SUFFIX.sub("", name or "").lower().replace("'", ""))


def _premiere_year(show: dict) -> int | None:
    y = (show.get("premiered") or "")[:4]
    return int(y) if y.isdigit() else None


def pick_best(shows: list[dict], query: str, year: int | None = None) -> dict | None:
    """The show a person most likely means by ``query``, from TVMaze's results (best first).

    TVMaze's own top hit is fuzzy: "Gone" can come back as "I'll Be Gone in the Dark".
    So an exact name wins, then a name that starts with the query's words, then one
    containing them all, then TVMaze's order. Ties go to the year hint, then English.
    With a year, only shows that premiered within a year of it are considered.
    """
    q, qwords = norm_name(query), _words(query)
    if year is not None:
        shows = [s for s in shows if (y := _premiere_year(s)) is not None and abs(y - year) <= 1]
    if not shows or not q:
        return shows[0] if shows else None

    def tier(show: dict) -> int:
        names = [show.get("name") or ""]
        if norm_name(names[0]) == q:
            return 0
        words = _words(names[0])
        if qwords and words[:len(qwords)] == qwords:
            return 1
        if qwords and all(w in words for w in qwords):
            return 2
        return 3

    def key(pair: tuple[int, dict]) -> tuple:
        pos, show = pair
        y = _premiere_year(show)
        return (tier(show),
                0 if (year is None or (y is not None and y == year)) else 1,
                0 if (show.get("language") or "English") == "English" else 1,
                pos)

    return min(enumerate(shows), key=key)[1]


async def lookup_by_name(title: str, year: int | None = None) -> dict | None:
    """The best match for a title; None only if TVMaze has no match. Raises TVMazeUnavailable."""
    return pick_best(await lookup_many(title), title, year)


async def lookup_many(title: str) -> list[dict]:
    """All matches for a title, best first. Raises TVMazeUnavailable."""
    data = await _strict_get("/search/shows", {"q": title})
    if not isinstance(data, list):
        return []
    return [r["show"] for r in data if isinstance(r, dict) and r.get("show")]


async def fetch_show(tvmaze_id: int) -> dict | None:
    """A show by TVMaze id; None only if it does not exist. Raises TVMazeUnavailable."""
    data = await _strict_get(f"/shows/{tvmaze_id}")
    return data if isinstance(data, dict) and data.get("id") else None


async def search_show(title: str) -> dict | None:
    """Search TVMaze for the best-matching show by title.

    Returns the show dict on success, None if not found or on error.
    """
    try:
        return await lookup_by_name(title)
    except TVMazeUnavailable as exc:
        logger.warning("TVMaze search failed for %r: %s", title, exc)
        return None


async def get_show(tvmaze_id: int) -> dict | None:
    """Fetch a show by its TVMaze ID.

    Returns the show dict on success, None if not found or on error.
    """
    try:
        return await fetch_show(tvmaze_id)
    except TVMazeUnavailable as exc:
        logger.warning("TVMaze get_show failed for id=%d: %s", tvmaze_id, exc)
        return None


async def get_episodes(tvmaze_id: int) -> list[dict] | None:
    """Fetch every episode of a show (specials included) from TVMaze.

    Returns None on any network/API error so callers can tell "unknown" apart
    from "the show has no episodes".
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await _get_json(client, f"/shows/{tvmaze_id}/episodes", {"specials": 1})
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else None
    except Exception as exc:
        logger.warning("TVMaze get_episodes failed for id=%d: %s", tvmaze_id, exc)
        return None


async def lookup_show(imdb: str | None = None, thetvdb: int | str | None = None) -> dict | None:
    """Find a show by external id (IMDb or TheTVDB); None if unknown or on error."""
    if imdb:
        params = {"imdb": imdb}
    elif thetvdb:
        params = {"thetvdb": str(thetvdb)}
    else:
        return None
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await _get_json(client, "/lookup/shows", params)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) and data.get("id") else None
    except Exception as exc:
        logger.warning("TVMaze lookup failed for %s: %s", params, exc)
        return None


async def search_shows(query: str) -> list[dict] | None:
    """Free-text show search; returns show dicts best-match first, None on error."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await _get_json(client, "/search/shows", {"q": query})
            resp.raise_for_status()
            return [r["show"] for r in resp.json() if isinstance(r, dict) and r.get("show")]
    except Exception as exc:
        logger.warning("TVMaze search_shows failed for %r: %s", query, exc)
        return None


async def get_schedule(date: str, country: str, web: bool = False) -> list[dict] | None:
    """Episodes airing on ``date`` (YYYY-MM-DD) with the show embedded.

    ``web`` selects streaming releases instead of broadcast TV.  None on error.
    """
    path = "/schedule/web" if web else "/schedule"
    params = {"date": date, "country": country}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await _get_json(client, path, params)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else None
    except Exception as exc:
        logger.warning("TVMaze schedule failed (%s %s %s): %s", path, date, country, exc)
        return None


async def get_show_status(title: str, tvmaze_id: int | None = None) -> tuple[str, int | None]:
    """Return (status_string, tvmaze_id) for the named show.

    Uses the cached tvmaze_id when available to skip the search step.
    Status strings are normalised through _STATUS_MAP; anything not in the
    map (including a missing/null status field) maps to "Unknown".
    Returns ("Unknown", None) on any network or API error.
    """
    show: dict | None = None

    if tvmaze_id is not None:
        show = await get_show(tvmaze_id)

    if show is None:
        show = await search_show(title)

    if show is None:
        return ("Unknown", None)

    resolved_id: int | None = show.get("id")
    raw_status: str | None = show.get("status")
    status = _STATUS_MAP.get(raw_status or "", "Unknown")
    return (status, resolved_id)
