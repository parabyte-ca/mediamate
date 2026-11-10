"""MusicBrainz (free, no key): find artists and list their albums.

MusicBrainz asks for a descriptive User-Agent and at most one request a second;
both are honoured here. Cover art comes from the Cover Art Archive by release group.
"""
import logging
import threading
import time

import httpx

logger = logging.getLogger(__name__)

API = "https://musicbrainz.org/ws/2"
COVER = "https://coverartarchive.org/release-group/{mbid}/front-250"
# Secondary types that aren't proper studio releases.
_SKIP_SECONDARY = {"compilation", "live", "soundtrack", "remix", "dj-mix", "mixtape/street", "demo",
                   "spokenword", "interview", "audiobook", "audio drama", "field recording"}

_lock = threading.Lock()
_last = [0.0]


class MusicBrainzUnavailable(Exception):
    pass


def _agent() -> str:
    import os
    v = os.environ.get("APP_VERSION", "0.8.0")
    return f"MediaMate/{v} ( https://github.com/parabyte-ca/mediamate )"


def _client() -> httpx.Client:
    return httpx.Client(timeout=15.0, headers={"User-Agent": _agent(), "Accept": "application/json"})


def _get(path: str, **params) -> dict:
    with _lock:                                   # one request a second, across threads
        wait = 1.0 - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()
    try:
        with _client() as http:
            r = http.get(f"{API}{path}", params={**params, "fmt": "json"})
    except httpx.HTTPError as exc:
        raise MusicBrainzUnavailable(str(exc)) from exc
    if r.status_code == 503:
        raise MusicBrainzUnavailable("MusicBrainz is busy; try again in a moment")
    if not r.is_success:
        raise MusicBrainzUnavailable(f"HTTP {r.status_code}")
    return r.json()


def rating(release_group_mbid: str) -> tuple[float | None, int]:
    """(average 0–5, votes) of a release group's community rating."""
    data = _get(f"/release-group/{release_group_mbid}", inc="ratings")
    r = data.get("rating") or {}
    return r.get("value"), int(r.get("votes-count") or 0)


def cover_url(release_group_mbid: str) -> str:
    return COVER.format(mbid=release_group_mbid)


def search_artists(query: str, limit: int = 8) -> list[dict]:
    data = _get("/artist", query=query, limit=limit)
    out = []
    for a in data.get("artists", []):
        life = a.get("life-span") or {}
        out.append({
            "mbid": a["id"], "name": a.get("name", ""), "disambiguation": a.get("disambiguation") or "",
            "country": a.get("country") or "", "type": a.get("type") or "",
            "begin": (life.get("begin") or "")[:4], "score": int(a.get("score") or 0),
        })
    return out


def artist(mbid: str) -> dict:
    a = _get(f"/artist/{mbid}")
    return {"mbid": a["id"], "name": a.get("name", ""), "disambiguation": a.get("disambiguation") or "",
            "country": a.get("country") or "", "type": a.get("type") or ""}


def albums(mbid: str, include_eps: bool = False) -> list[dict]:
    """The artist's studio albums (and EPs), oldest first: {mbid, title, type, date}."""
    types = "album|ep" if include_eps else "album"
    out, offset = [], 0
    while True:
        data = _get("/release-group", artist=mbid, type=types, limit=100, offset=offset)
        groups = data.get("release-groups", [])
        for g in groups:
            if {s.lower() for s in g.get("secondary-types") or []} & _SKIP_SECONDARY:
                continue
            out.append({"mbid": g["id"], "title": g.get("title", ""), "type": g.get("primary-type") or "Album",
                        "date": g.get("first-release-date") or ""})
        offset += len(groups)
        if not groups or offset >= int(data.get("release-group-count") or 0) or offset >= 500:
            break
    return sorted(out, key=lambda g: (g["date"] or "9999", g["title"].lower()))


def release_group_artist(release_group_mbid: str) -> dict | None:
    """The main credited artist of a release group: {mbid, name}, or None."""
    data = _get(f"/release-group/{release_group_mbid}", inc="artist-credits")
    for credit in data.get("artist-credit") or []:
        a = credit.get("artist") if isinstance(credit, dict) else None
        if a and a.get("id"):
            return {"mbid": a["id"], "name": a.get("name", "")}
    return None


def find_release_group(artist_name: str, album_title: str) -> str | None:
    """The best-matching release group's id for an artist and album title, or None."""
    q = f'releasegroup:"{album_title}" AND artist:"{artist_name}"'
    groups = _get("/release-group", query=q, limit=5).get("release-groups", [])
    groups = [g for g in groups if int(g.get("score") or 0) >= 90]
    return groups[0]["id"] if groups else None


def releases(release_group_mbid: str) -> list[dict]:
    """The group's releases with their track lists:
    [{mbid, title, date, artist, tracks: [{disc, no, title, artist}]}]."""
    data = _get("/release", **{"release-group": release_group_mbid, "inc": "recordings+artist-credits",
                               "limit": 25})
    out = []
    for r in data.get("releases", []):
        credit = "".join(c.get("name", "") + c.get("joinphrase", "") for c in r.get("artist-credit") or [])
        tracks = []
        for disc_no, medium in enumerate(r.get("media") or [], 1):
            for t in medium.get("tracks") or []:
                tc = "".join(c.get("name", "") + c.get("joinphrase", "") for c in t.get("artist-credit") or [])
                tracks.append({"disc": disc_no, "no": int(t.get("position") or 0), "title": t.get("title", ""),
                               "artist": tc or credit})
        out.append({"mbid": r["id"], "title": r.get("title", ""), "date": r.get("date") or "",
                    "artist": credit, "discs": len(r.get("media") or []), "tracks": tracks})
    return out


def cover_bytes(release_group_mbid: str) -> bytes | None:
    """The front cover (500 px) from the Cover Art Archive, or None."""
    try:
        with _client() as http:
            r = http.get(COVER.format(mbid=release_group_mbid).replace("-250", "-500"), follow_redirects=True)
        return r.content if r.is_success and r.content else None
    except httpx.HTTPError:
        return None


def search_release_groups(artist_name: str | None, album_title: str | None, text: str | None = None,
                          limit: int = 12) -> list[dict]:
    """Release groups to pick a cover from, live albums and bootleg-ish ones included:
    [{mbid, title, artist, date, type, cover}]."""
    if text:
        q = text
    else:
        parts = []
        if album_title:
            parts.append(f'releasegroup:"{album_title}"')
        if artist_name:
            parts.append(f'artist:"{artist_name}"')
        q = " AND ".join(parts) or "*"
    out = []
    for g in _get("/release-group", query=q, limit=limit).get("release-groups", []):
        credit = "".join(c.get("name", "") + c.get("joinphrase", "") for c in g.get("artist-credit") or [])
        kinds = [g.get("primary-type") or ""] + list(g.get("secondary-types") or [])
        out.append({"mbid": g["id"], "title": g.get("title", ""), "artist": credit,
                    "date": g.get("first-release-date") or "", "type": " · ".join(k for k in kinds if k),
                    "cover": cover_url(g["id"])})
    return out
