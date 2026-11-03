"""Minimal TMDB client: find a movie's poster and blurb.

TV shows come from TVMaze (free, no key).  TMDB covers movies, and needs the
user's own free API key — either the v3 "API Key" or the v4 "Read Access Token".
"""
import logging
import os
import re
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://api.themoviedb.org/3"
_IMAGE_BASE = "https://image.tmdb.org/t/p/w342"

# fullmatch, never match+$: a trailing newline must not reach an HTTP header or URL.
_V3_KEY = re.compile(r"[0-9a-fA-F]{32}")
_V4_TOKEN = re.compile(r"[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}")


class TMDBUnavailable(Exception):
    """TMDB could not answer (network, rate limit, outage, rejected key).

    Distinct from "no such movie": callers must not remember it as a negative result.
    """


def valid_key(key: str | None) -> bool:
    key = key or ""
    return bool(_V3_KEY.fullmatch(key) or _V4_TOKEN.fullmatch(key))


def _auth(key: str) -> tuple[dict[str, str], dict[str, str]]:
    headers = {"Accept": "application/json", "User-Agent": f"ScreenMate/{os.environ.get('APP_VERSION', 'dev')}"}
    params: dict[str, str] = {}
    if _V3_KEY.fullmatch(key):
        params["api_key"] = key
    else:
        headers["Authorization"] = f"Bearer {key}"
    return headers, params


async def _get(key: str, path: str, params: dict | None = None):
    if not valid_key(key):
        raise TMDBUnavailable("That doesn't look like a TMDB API key.")
    headers, auth_params = _auth(key)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{_BASE}{path}", headers=headers, params={**auth_params, **(params or {})})
    except httpx.HTTPError as exc:
        raise TMDBUnavailable(str(exc) or type(exc).__name__) from exc
    if resp.status_code in (401, 403):
        raise TMDBUnavailable("TMDB rejected the API key.")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TMDBUnavailable(f"HTTP {resp.status_code}")
    try:
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TMDBUnavailable(str(exc) or type(exc).__name__) from exc


def poster_url(movie: dict) -> str | None:
    path = movie.get("poster_path")
    return f"{_IMAGE_BASE}{path}" if isinstance(path, str) and path.startswith("/") else None


async def search_movie(key: str, name: str, year: int | None = None) -> dict | None:
    """Best TMDB movie match with a poster; None if TMDB has none. Raises TMDBUnavailable.

    With a year, only a movie released within a year of it is accepted (so
    "Dune 1984" is not answered with the 2021 film); without one, the most
    popular match wins.
    """
    params: dict = {"query": name, "include_adult": "false"}
    data = await _get(key, "/search/movie", params)
    results = data.get("results") if isinstance(data, dict) else None
    for movie in results or []:
        if not isinstance(movie, dict) or not movie.get("id") or not poster_url(movie):
            continue
        if year is not None:
            released = (movie.get("release_date") or "")[:4]
            if not (released.isdigit() and abs(int(released) - year) <= 1):
                continue
        return movie
    return None


async def check_key(key: str) -> tuple[bool, str]:
    """Validate a key against TMDB: (ok, message). Never raises."""
    if not valid_key(key):
        return False, "That doesn't look like a TMDB API key (32 hex characters, or the long read-access token)."
    try:
        await _get(key, "/configuration")
    except TMDBUnavailable as exc:
        return False, str(exc)
    return True, "Connected to TMDB."


def link(movie_id: int) -> str:
    return f"https://www.themoviedb.org/movie/{urllib.parse.quote(str(movie_id))}"


# ── Release dates (movies on the watchlist) ───────────────────────────────────

# TMDB release types: 1 premiere, 2 limited theatrical, 3 theatrical, 4 digital, 5 physical, 6 TV.
DIGITAL, PHYSICAL, THEATRICAL = 4, 5, 3
# No digital or disc date yet: good releases usually turn up about this long after cinemas.
THEATRICAL_TO_DIGITAL_DAYS = 90


async def movie(key: str, tmdb_id: int) -> dict:
    """A movie's details. Raises TMDBUnavailable (also when TMDB doesn't know the id)."""
    data = await _get(key, f"/movie/{int(tmdb_id)}")
    if not isinstance(data, dict) or not data.get("id"):
        raise TMDBUnavailable("TMDB has no such movie.")
    return data


def pick_release(results: list, country: str = "US", fallback_date: str | None = None) -> tuple[str | None, str | None]:
    """(YYYY-MM-DD, "digital" | "physical" | "estimated") from TMDB release_dates results.

    The earliest digital release, else physical, in ``country`` then the US; else the
    theatrical date plus THEATRICAL_TO_DIGITAL_DAYS (an estimate); else (None, None).
    """
    from datetime import date, timedelta

    by_country: dict[str, list[dict]] = {}
    for r in results or []:
        if isinstance(r, dict) and isinstance(r.get("release_dates"), list):
            by_country[str(r.get("iso_3166_1") or "").upper()] = [d for d in r["release_dates"] if isinstance(d, dict)]

    def valid(day: str) -> bool:
        try:
            date.fromisoformat(day)
            return True
        except ValueError:
            return False

    def earliest(entries: list[dict], kind: int) -> str | None:
        days = sorted(day for d in entries if d.get("type") == kind
                      for day in [str(d.get("release_date") or "")[:10]] if valid(day))
        return days[0] if days else None

    order = [country.upper()] + (["US"] if country.upper() != "US" else [])
    for kind, label in ((DIGITAL, "digital"), (PHYSICAL, "physical")):
        for c in order:
            found = earliest(by_country.get(c, []), kind)
            if found:
                return found, label
    theatrical = None
    for c in order:
        theatrical = earliest(by_country.get(c, []), THEATRICAL) or earliest(by_country.get(c, []), 2)
        if theatrical:
            break
    theatrical = theatrical or (fallback_date or "")[:10] or None
    try:
        start = date.fromisoformat(theatrical) if theatrical else None
    except ValueError:
        start = None
    if start is None:
        return None, None
    return (start + timedelta(days=THEATRICAL_TO_DIGITAL_DAYS)).isoformat(), "estimated"


async def release_info(key: str, tmdb_id: int, country: str = "US",
                       fallback_date: str | None = None) -> tuple[str | None, str | None]:
    """When a movie's downloadable release is (see pick_release). Raises TMDBUnavailable."""
    data = await _get(key, f"/movie/{int(tmdb_id)}/release_dates")
    return pick_release((data or {}).get("results") or [], country, fallback_date)
