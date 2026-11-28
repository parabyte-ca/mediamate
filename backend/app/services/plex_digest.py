"""Weekly Plex digest: what was added this week, emailed to the household.

"This week from MediaMate". The email has a highlight (the best-rated new release), new movies as a poster grid,
TV grouped by show ("3 new episodes · S01E04–06"), albums added to the music library,
and what's coming next week from the Watchlist. Posters are attached to the email
(cid: images) because Gmail and others block data: images; the browser preview and the
"View in browser" copy use data: images instead. A plain-text part goes with the HTML.
"""

import base64
import html as _html
import logging
import re
import secrets
import smtplib
import ssl
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_NON_MATURE_MOVIE = {"G", "PG", "PG-13"}
_NON_MATURE_TV    = {"TV-Y", "TV-Y7", "TV-G", "TV-PG", "TV-14"}


def _fetch_machine_identifier(plex_url: str, token: str) -> str:
    """Return the Plex server machineIdentifier used for building deep links."""
    try:
        resp = httpx.get(
            plex_url.rstrip("/") + "/",
            params={"X-Plex-Token": token},
            timeout=8.0,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        return root.get("machineIdentifier", "")
    except Exception as e:
        logger.warning("Plex machine identifier fetch failed: %s", e)
    return ""


def _get_library_ids_by_type(
    plex_url: str,
    token: str,
    lib_type: str,
    excluded_names: frozenset[str] = frozenset(),
) -> list[str]:
    """Return section IDs of the given type, skipping excluded library names (case-insensitive)."""
    url = f"{plex_url.rstrip('/')}/library/sections"
    try:
        resp = httpx.get(url, params={"X-Plex-Token": token}, timeout=10.0)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        return [
            d.get("key")
            for d in root.findall(".//Directory")
            if d.get("type") == lib_type
            and d.get("key")
            and d.get("title", "").lower() not in excluded_names
        ]
    except Exception as e:
        logger.error("Plex library listing failed: %s", e)
        return []


def _fetch_recently_added(plex_url: str, token: str, library_id: str, days: int = 7) -> list[dict]:
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    url = f"{plex_url.rstrip('/')}/library/sections/{library_id}/recentlyAdded"
    try:
        resp = httpx.get(
            url,
            params={"X-Plex-Token": token, "X-Plex-Container-Size": "500"},
            timeout=15.0,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("Plex recentlyAdded failed (library %s): %s", library_id, e)
        return []
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        logger.error("Plex XML parse error: %s", e)
        return []

    items = []
    for elem in root:
        added_at = int(elem.get("addedAt") or 0)
        if added_at < cutoff:
            continue
        items.append({
            "title":             elem.get("title", ""),
            "year":              elem.get("year", ""),
            "summary":           (elem.get("summary") or "").strip(),
            "rating":            elem.get("rating", "") or elem.get("audienceRating", ""),
            "audience_rating":   elem.get("audienceRating", ""),
            "content_rating":    elem.get("contentRating", ""),
            "genres":            [g.get("tag") for g in elem.findall("Genre") if g.get("tag")],
            "item_type":         elem.get("type", ""),
            "thumb":             elem.get("thumb", ""),
            "rating_key":        elem.get("ratingKey", ""),
            "grandparent_title": elem.get("grandparentTitle", ""),
            "grandparent_key":   elem.get("grandparentKey", ""),
            "grandparent_thumb": elem.get("grandparentThumb", ""),
            "season_num":        elem.get("parentIndex", ""),
            "episode_num":       elem.get("index", ""),
            "added_at":          added_at,
            "released":          elem.get("originallyAvailableAt", ""),
        })
    return items


def _fetch_show_summary(plex_url: str, token: str, show_key: str) -> str:
    """Fetch the summary for a show using its grandparentKey."""
    if not show_key:
        return ""
    url = f"{plex_url.rstrip('/')}{show_key}"
    try:
        resp = httpx.get(url, params={"X-Plex-Token": token}, timeout=8.0)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        elem = root.find(".//Directory")
        if elem is not None:
            return (elem.get("summary") or "").strip()
    except Exception as e:
        logger.warning("Failed to fetch show summary for %s: %s", show_key, e)
    return ""


def _fetch_image(plex_url: str, token: str, thumb_path: str, width: int = 200, height: int = 300) -> bytes | None:
    """Download a Plex poster at the given size; JPEG bytes, or None."""
    if not thumb_path:
        return None
    url = f"{plex_url.rstrip('/')}/photo/:/transcode"
    try:
        resp = httpx.get(
            url,
            params={"url": thumb_path, "width": str(width), "height": str(height),
                    "minSize": "1", "X-Plex-Token": token},
            timeout=8.0,
        )
        if resp.is_success and resp.content:
            return resp.content
    except Exception as e:
        logger.warning("Thumbnail fetch failed for %s: %s", thumb_path, e)
    return None


def _fetch_thumb_b64(plex_url: str, token: str, thumb_path: str) -> str:
    """Older callers: the poster as a data: URI (small)."""
    data = _fetch_image(plex_url, token, thumb_path, 80, 120)
    return f"data:image/jpeg;base64,{base64.b64encode(data).decode()}" if data else ""


_FILEISH = re.compile(r"(?i)(\.(mkv|mp4|avi|m4v|mov|wmv|ts)$|\b(2160p|1080p|720p|480p|x26[45]|h\.?26[45]|hevc|web-?dl|webrip|bluray|hdtv)\b|\bS\d{1,2}E\d{1,3}\b)")


def release_year(item: dict) -> int:
    """The year a title (or episode) came out, from its air date or year; 0 when unknown."""
    for v in (item.get("released") or "", str(item.get("year") or "")):
        m = re.match(r"(\d{4})", v)
        if m:
            return int(m[1])
    return 0


def is_vague(item: dict) -> bool:
    """A file Plex couldn't match (often in an _Unsorted folder): no year and no summary,
    or a title that is really a file name. Left out of the digest."""
    title = (item.get("title") or "").strip()
    if not title or title.startswith("_") or title.lower() in ("unsorted", "unknown"):
        return True
    if _FILEISH.search(title) or ("." in title and " " not in title):
        return True
    return not release_year(item) and not (item.get("summary") or "").strip()


def is_vague_show(show_title: str, ep: dict) -> bool:
    """An episode whose show is unknown (an _Unsorted folder, a file-name title)."""
    t = (show_title or "").strip()
    if not t or t.startswith("_") or t.lower() in ("unsorted", "unknown"):
        return True
    return bool(_FILEISH.search(t)) or ("." in t and " " not in t)


def episode_ranges(codes: list[str]) -> str:
    """["S01E04","S01E05","S01E06","S02E01"] → "S01E04–06, S02E01"."""
    eps = []
    for c in sorted(set(codes)):
        m = re.match(r"S(\d+)E(\d+)", c)
        if m:
            eps.append((int(m[1]), int(m[2])))
    eps.sort()
    out, i = [], 0
    while i < len(eps):
        s_, start = eps[i]
        end = start
        while i + 1 < len(eps) and eps[i + 1] == (s_, end + 1):
            i += 1
            end += 1
        out.append(f"S{s_:02d}E{start:02d}" + (f"–{end:02d}" if end > start else ""))
        i += 1
    return ", ".join(out)


def fetch_digest_sections(
    plex_url: str,
    token: str,
    days: int = 7,
    excluded_libs: frozenset[str] = frozenset(),
) -> dict[str, list]:
    """Return four content buckets with cover art and summaries.

    All Plex movie and show libraries are queried automatically.
    Libraries whose names (case-insensitive) appear in excluded_libs are skipped.
    """
    sections: dict[str, list] = {
        "movies": [], "mature_movies": [], "tv": [], "mature_tv": [],
    }

    machine_id = _fetch_machine_identifier(plex_url, token)

    def _plex_link(rating_key: str) -> str:
        if not machine_id or not rating_key:
            return ""
        return (
            f"https://app.plex.tv/desktop/#!/server/{machine_id}"
            f"/details?key=%2Flibrary%2Fmetadata%2F{rating_key}"
        )

    # ── Movies ────────────────────────────────────────────────────────────────
    for lib_id in _get_library_ids_by_type(plex_url, token, "movie", excluded_libs):
        for item in _fetch_recently_added(plex_url, token, lib_id, days):
            if is_vague(item):
                continue
            item["image"] = _fetch_image(plex_url, token, item["thumb"])
            item["plex_link"] = _plex_link(item.get("rating_key", ""))
            cr = (item["content_rating"] or "").upper().strip()
            bucket = "movies" if cr in _NON_MATURE_MOVIE else "mature_movies"
            sections[bucket].append(item)

    # ── TV shows ──────────────────────────────────────────────────────────────
    shows: dict[str, dict] = {}
    for lib_id in _get_library_ids_by_type(plex_url, token, "show", excluded_libs):
        for ep in _fetch_recently_added(plex_url, token, lib_id, days):
            show_title = ep["grandparent_title"] or ep["title"]
            if is_vague_show(show_title, ep):
                continue
            if show_title not in shows:
                cr = (ep["content_rating"] or "").upper().strip()
                gp_key = ep["grandparent_key"]  # "/library/metadata/{id}"
                gp_rating_key = gp_key.rsplit("/", 1)[-1] if gp_key else ""
                shows[show_title] = {
                    "title":          show_title,
                    "content_rating": cr,
                    "episodes":       [],
                    "added_at":       ep["added_at"],
                    "summary":        "",
                    "plex_link":      _plex_link(gp_rating_key),
                    "grandparent_key":   gp_key,
                    "grandparent_thumb": ep["grandparent_thumb"],
                    "image":          None,
                    "rating":         ep.get("rating", ""),
                }
            shows[show_title]["release_year"] = max(shows[show_title].get("release_year", 0), release_year(ep))
            s_num = ep.get("season_num", "")
            e_num = ep.get("episode_num", "")
            if s_num and e_num:
                try:
                    shows[show_title]["episodes"].append(
                        f"S{int(s_num):02d}E{int(e_num):02d}"
                    )
                except (ValueError, TypeError):
                    pass

    # Fetch show summaries and thumbnails (one call per unique show)
    for show in shows.values():
        show["summary"]   = _fetch_show_summary(plex_url, token, show["grandparent_key"])
        show["image"] = _fetch_image(plex_url, token, show["grandparent_thumb"])
        show["episode_ranges"] = episode_ranges(show["episodes"])
        cr = show["content_rating"]
        bucket = "tv" if cr in _NON_MATURE_TV else "mature_tv"
        sections[bucket].append(show)

    return sections




# ── the rest of the digest: highlight, music, coming next week ──────────────

def _score(item: dict) -> float:
    try:
        return float(item.get("rating") or 0)
    except (TypeError, ValueError):
        return 0.0


def _popularity(item: dict) -> float:
    """How well liked a title is: Plex's audience rating, else its critic rating."""
    try:
        return float(item.get("audience_rating") or 0) or _score(item)
    except (TypeError, ValueError):
        return _score(item)


def _year_of(kind: str, it: dict) -> int:
    return it.get("release_year", 0) if "tv" in kind else release_year(it)


def _best(pool: list[tuple[str, dict]], this_year: int, score=_score) -> tuple[str, dict]:
    """A new release, not an old film that happened to be added: among titles out this
    year or last, the best scored; failing that, the newest (then best scored)."""
    fresh = [p for p in pool if _year_of(*p) >= this_year - 1]
    if fresh:
        return max(fresh, key=lambda p: (score(p[1]), _year_of(*p), p[1].get("added_at", 0)))
    return max(pool, key=lambda p: (_year_of(*p), score(p[1]), p[1].get("added_at", 0)))


def pick_highlight(sections: dict[str, list], this_year: int | None = None) -> dict | None:
    """This week's highlight: the best new release. Family-friendly titles first, since
    the digest goes to the whole household."""
    this_year = this_year or datetime.now(timezone.utc).year
    for keys in (("movies", "tv"), ("mature_movies", "mature_tv")):
        pool = [(k, i) for k in keys for i in sections.get(k, [])]
        if pool:
            kind, item = _best(pool, this_year)
            return {"kind": "movie" if "movies" in kind else "tv", "item": item}
    return None


def pick_mature_movie(sections: dict[str, list], highlight: dict | None = None,
                      this_year: int | None = None) -> dict | None:
    """The mature movie of the week: the most recent release that's also the most
    popular (audience rating), among mature movies added this week. None when there
    are none, or when it's already the main highlight."""
    this_year = this_year or datetime.now(timezone.utc).year
    taken = highlight["item"] if highlight else None
    pool = [("mature_movies", i) for i in sections.get("mature_movies", []) if i is not taken]
    if not pool:
        return None
    _, item = _best(pool, this_year, score=_popularity)
    return {"kind": "movie", "item": item}


def music_added(db, days: int = 7, limit: int = 12) -> list[dict]:
    """Albums that arrived in the music library this week, newest first, with small covers."""
    from ..models import MusicLibAlbum
    from . import music_covers
    import os
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    rows = (db.query(MusicLibAlbum).filter(MusicLibAlbum.added_at.isnot(None))
            .order_by(MusicLibAlbum.added_at.desc()).limit(200).all())
    for a in rows:
        at = a.added_at if a.added_at.tzinfo else a.added_at.replace(tzinfo=timezone.utc)
        if at < cutoff:
            break
        image = None
        if a.cover:
            try:
                path = music_covers.thumb(a.id, os.path.join(a.folder, a.cover), 160)
                image = Path(path).read_bytes()
                if len(image) > 400_000:          # the original, too big for an email
                    image = None
            except Exception:
                image = None
        out.append({"title": a.title or "Untitled", "artist": a.artist or "", "year": a.year,
                    "format": (a.format or "").upper(), "tracks": a.tracks, "image": image})
        if len(out) >= limit:
            break
    return out


def coming_up(db, days: int = 7) -> dict:
    """Next week: episodes airing for Watchlist shows, and Watchlist movies coming out."""
    import asyncio
    from ..models import WatchlistItem
    from .calendar_view import build_calendar
    shows: dict[str, dict] = {}
    try:
        cal = asyncio.run(build_calendar(db, days_back=0, days_ahead=days))
        for e in cal.get("entries", []):
            if not e.get("enabled", True):
                continue
            sh = shows.setdefault(e["title"], {"title": e["title"], "poster_url": e.get("poster_url"), "episodes": []})
            sh["episodes"].append({"code": f"S{e['season']:02d}E{e['episode']:02d}", "name": e.get("name") or "",
                                   "airs_at": e["airs_at"]})
    except Exception as exc:
        logger.warning("Digest: couldn't read the calendar: %s", exc)
    today = date.today()
    soon = (today + timedelta(days=days)).isoformat()
    movies = []
    for m in db.query(WatchlistItem).filter(WatchlistItem.kind == "movie"):
        rd = m.release_date or ""
        if rd and today.isoformat() <= rd <= soon and not m.completed_at:
            movies.append({"title": m.title, "year": m.year, "release_date": rd, "poster_url": m.poster_url})
    movies.sort(key=lambda m: m["release_date"])
    show_list = sorted(shows.values(), key=lambda s: s["episodes"][0]["airs_at"])
    return {"shows": show_list, "movies": movies}


def build_digest(db, plex_url: str, token: str, excluded_libs: frozenset[str] = frozenset(),
                 days: int = 7) -> dict:
    """Everything the email shows. Plex, the music library and the Watchlist are each
    optional: one failing leaves its section out."""
    sections = fetch_digest_sections(plex_url, token, days=days, excluded_libs=excluded_libs)
    hl = pick_highlight(sections)
    mature = pick_mature_movie(sections, hl)
    for pick in (hl, mature):
        if pick is not None:
            it = pick["item"]
            thumb = it.get("thumb") or it.get("grandparent_thumb")
            big = _fetch_image(plex_url, token, thumb, 320, 480) if thumb else None
            pick["image"] = big or it.get("image")
    music = []
    try:
        music = music_added(db, days)
    except Exception as exc:
        logger.warning("Digest: couldn't list new albums: %s", exc)
    return {"sections": sections, "highlight": hl, "mature_pick": mature, "music": music,
            "coming": coming_up(db, days)}


# ── rendering ───────────────────────────────────────────────────────────────

_SECTION_DEFS = (
    ("movies", "New movies", "movie"),
    ("mature_movies", "New movies · Mature", "movie"),
    ("tv", "New episodes", "tv"),
    ("mature_tv", "New episodes · Mature", "tv"),
)

# Colours: a light email that dark-mode clients can flip (classes below).
_INK, _MUTED, _FAINT, _LINE, _CARD, _BG, _ACCENT = "#111827", "#4b5563", "#9ca3af", "#e5e7eb", "#ffffff", "#f3f4f6", "#2563eb"


def _esc(s) -> str:
    return _html.escape(str(s or ""), quote=True)


def _clip(text: str, limit: int) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


def _day(iso: str) -> str:
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00")) if "T" in iso else datetime.fromisoformat(iso)
        return d.strftime("%a %b %-d")
    except Exception:
        return iso[:10]


def counts(d: dict) -> dict:
    s = d["sections"]
    return {"movies": len(s.get("movies", [])) + len(s.get("mature_movies", [])),
            "episodes": sum(len(set(x.get("episodes", []))) for k in ("tv", "mature_tv") for x in s.get(k, [])),
            "shows": len(s.get("tv", [])) + len(s.get("mature_tv", [])),
            "albums": len(d.get("music", []))}


def render_html(d: dict, week_label: str, inline_images: bool = False, view_url: str = "") -> tuple[str, dict]:
    """(html, images). images = {cid: jpeg bytes} to attach; empty with inline_images
    (preview and the browser copy), where pictures are data: URIs instead."""
    images: dict[str, bytes] = {}

    def src(data: bytes | None, name: str) -> str:
        if not data:
            return ""
        if inline_images:
            return f"data:image/jpeg;base64,{base64.b64encode(data).decode()}"
        cid = f"{name}-{len(images)}@mediamate"
        images[cid] = data
        return f"cid:{cid}"

    def poster(data, name, w, h, link="", radius=8):
        u = src(data, name)
        box = (f'<img src="{u}" width="{w}" height="{h}" alt="" style="display:block;width:100%;max-width:{w}px;'
               f'height:auto;border-radius:{radius}px;border:0">' if u else
               f'<div style="width:100%;max-width:{w}px;height:{h}px;background:{_LINE};border-radius:{radius}px"></div>')
        return f'<a href="{_esc(link)}" target="_blank" style="text-decoration:none">{box}</a>' if link and u else box

    def button(label, link):
        if not link:
            return ""
        return (f'<a href="{_esc(link)}" target="_blank" style="display:inline-block;background:{_ACCENT};color:#ffffff;'
                f'font-size:14px;font-weight:600;text-decoration:none;padding:10px 18px;border-radius:999px">{label}</a>')

    def heading(text, n=None):
        badge = f' <span style="color:{_FAINT};font-weight:400">({n})</span>' if n else ""
        return (f'<tr><td class="mm-ink" style="padding:28px 0 10px;font-size:18px;font-weight:700;color:{_INK}">'
                f'{text}{badge}</td></tr>')

    parts: list[str] = []
    c = counts(d)
    summary_bits = [f"{c['movies']} movie{'s' if c['movies'] != 1 else ''}" if c["movies"] else "",
                    f"{c['episodes']} episode{'s' if c['episodes'] != 1 else ''}" if c["episodes"] else "",
                    f"{c['albums']} album{'s' if c['albums'] != 1 else ''}" if c["albums"] else ""]
    summary = " · ".join(b for b in summary_bits if b) or "A quiet week"

    def feature(pick, label, name):
        """A large card: the week's highlight, or the mature pick."""
        it = pick["item"]
        if pick["kind"] == "movie":
            pop = _popularity(it)
            meta = [str(it.get("year") or ""), it.get("content_rating") or "",
                    f"★ {pop:.1f}" if pop else "", ", ".join((it.get("genres") or [])[:2])]
        else:
            n = len(set(it.get("episodes", [])))
            meta = [it.get("content_rating") or "", f"{n} new episode{'s' if n != 1 else ''}",
                    it.get("episode_ranges") or ""]
        return (
            f'<tr><td style="padding-top:20px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'class="mm-card mm-hl" style="background:{_CARD};border-radius:14px"><tr>'
            f'<td width="40%" style="padding:16px;vertical-align:top">{poster(pick.get("image"), name, 220, 330, it.get("plex_link") or "", 10)}</td>'
            f'<td style="padding:16px 16px 16px 4px;vertical-align:top">'
            f'<div style="font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:{_ACCENT}">{label}</div>'
            f'<div class="mm-ink" style="font-size:22px;font-weight:800;line-height:1.25;color:{_INK};margin-top:6px">{_esc(it["title"])}</div>'
            f'<div class="mm-muted" style="font-size:13px;color:{_MUTED};margin-top:4px">{_esc(" · ".join(m for m in meta if m))}</div>'
            f'<div class="mm-muted" style="font-size:14px;line-height:1.55;color:{_MUTED};margin-top:10px">{_esc(_clip(it.get("summary") or "", 320))}</div>'
            f'<div style="margin-top:14px">{button("▶ Watch in Plex", it.get("plex_link") or "")}</div>'
            f'</td></tr></table></td></tr>')

    # Highlight
    hl = d.get("highlight")
    if hl:
        parts.append(feature(hl, "This week’s highlight", "hl"))
    mature = d.get("mature_pick")

    featured = [p["item"] for p in (hl, mature) if p]

    # Movies (grid) and TV (grouped)
    for key, title, kind in _SECTION_DEFS:
        if key == "mature_movies" and mature:
            parts.append(feature(mature, "Mature pick of the week", "mp"))
        items = [i for i in d["sections"].get(key, []) if not any(i is f for f in featured)]
        if not items:
            continue
        parts.append(heading(title, len(items)))
        if kind == "movie":
            cells = []
            for m in items:
                meta = " · ".join(x for x in [str(m.get("year") or ""), f"★ {_score(m):.1f}" if _score(m) else ""] if x)
                cells.append(
                    f'<td width="33%" style="padding:0 4px 16px;vertical-align:top">'
                    f'{poster(m.get("image"), "m", 180, 270, m.get("plex_link") or "")}'
                    f'<div class="mm-ink" style="font-size:13px;font-weight:600;color:{_INK};margin-top:6px;line-height:1.3">{_esc(m["title"])}</div>'
                    f'<div class="mm-faint" style="font-size:12px;color:{_FAINT}">{_esc(meta)}</div></td>')
            rows = ["".join(cells[i:i + 3]) + ('<td width="33%"></td>' * (3 - len(cells[i:i + 3])))
                    for i in range(0, len(cells), 3)]
            parts.append('<tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0" >'
                         + "".join(f"<tr>{r}</tr>" for r in rows) + "</table></td></tr>")
        else:
            for sh in items:
                n = len(set(sh.get("episodes", [])))
                line = f"{n} new episode{'s' if n != 1 else ''}" + (f" · {sh.get('episode_ranges')}" if sh.get("episode_ranges") else "")
                link = sh.get("plex_link") or ""
                parts.append(
                    f'<tr><td style="padding:0 0 12px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                    f'class="mm-card" style="background:{_CARD};border-radius:12px"><tr>'
                    f'<td width="72" style="padding:12px;vertical-align:top">{poster(sh.get("image"), "tv", 60, 90, link, 6)}</td>'
                    f'<td style="padding:12px 12px 12px 0;vertical-align:top">'
                    f'<div class="mm-ink" style="font-size:15px;font-weight:700;color:{_INK}">{_esc(sh["title"])}</div>'
                    f'<div style="font-size:13px;font-weight:600;color:{_ACCENT};margin-top:2px">{_esc(line)}</div>'
                    + (f'<div class="mm-muted" style="font-size:13px;line-height:1.5;color:{_MUTED};margin-top:6px">{_esc(_clip(sh.get("summary") or "", 160))}</div>' if sh.get("summary") else "")
                    + (f'<div style="margin-top:6px"><a href="{_esc(link)}" target="_blank" style="font-size:13px;color:{_ACCENT};text-decoration:none">Watch in Plex →</a></div>' if link else "")
                    + "</td></tr></table></td></tr>")

    # Music
    music = d.get("music") or []
    if music:
        parts.append(heading("New music", len(music)))
        cells = [f'<td width="33%" style="padding:0 4px 16px;vertical-align:top">{poster(a.get("image"), "al", 160, 160, "", 8)}'
                 f'<div class="mm-ink" style="font-size:13px;font-weight:600;color:{_INK};margin-top:6px;line-height:1.3">{_esc(a["title"])}</div>'
                 f'<div class="mm-faint" style="font-size:12px;color:{_FAINT}">{_esc(" · ".join(x for x in [a["artist"], str(a["year"] or "")] if x))}</div></td>'
                 for a in music]
        rows = ["".join(cells[i:i + 3]) + ('<td width="33%"></td>' * (3 - len(cells[i:i + 3]))) for i in range(0, len(cells), 3)]
        parts.append('<tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="table-layout:fixed">'
                     + "".join(f"<tr>{r}</tr>" for r in rows) + "</table></td></tr>")

    # Coming next week
    coming = d.get("coming") or {}
    if coming.get("shows") or coming.get("movies"):
        parts.append(heading("Coming next week"))
        rows = []
        for sh in coming.get("shows", []):
            eps = sh["episodes"]
            first = eps[0]
            more = f" + {len(eps) - 1} more" if len(eps) > 1 else ""
            rows.append((first["airs_at"], sh["title"], f'{first["code"]}' + (f' “{first["name"]}”' if first["name"] else "") + more))
        for m in coming.get("movies", []):
            rows.append((m["release_date"], m["title"] + (f' ({m["year"]})' if m.get("year") else ""), "Movie · out digitally"))
        rows.sort(key=lambda r: r[0])
        parts.append('<tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="mm-card" '
                     f'style="background:{_CARD};border-radius:12px">'
                     + "".join(
                         f'<tr><td width="92" class="mm-faint" style="padding:10px 0 10px 14px;font-size:13px;font-weight:600;color:{_FAINT};'
                         f'vertical-align:top;border-top:{"0" if i == 0 else "1px solid " + _LINE}">{_esc(_day(when))}</td>'
                         f'<td style="padding:10px 14px 10px 8px;vertical-align:top;border-top:{"0" if i == 0 else "1px solid " + _LINE}">'
                         f'<div class="mm-ink" style="font-size:14px;font-weight:600;color:{_INK}">{_esc(t)}</div>'
                         f'<div class="mm-muted" style="font-size:13px;color:{_MUTED}">{_esc(sub)}</div></td></tr>'
                         for i, (when, t, sub) in enumerate(rows))
                     + "</table></td></tr>")

    if len(parts) == 0:
        parts.append(f'<tr><td class="mm-muted" style="padding:40px 0;text-align:center;color:{_MUTED};font-size:15px">'
                     "Nothing new was added this week.</td></tr>")

    view = (f'<a href="{_esc(view_url)}" target="_blank" style="color:{_FAINT}">View in browser</a> · ' if view_url else "")
    html = (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"UTF-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
        "<meta name=\"color-scheme\" content=\"light dark\">\n<meta name=\"supported-color-schemes\" content=\"light dark\">\n"
        f"<title>{_esc('This week from MediaMate · ' + week_label)}</title>\n"
        "<style>\n"
        "@media (max-width:480px){ .mm-wrap{padding:12px 8px!important} .mm-hl td{display:block!important;width:100%!important;box-sizing:border-box!important} .mm-hl td:first-child{padding-bottom:0!important} }\n"
        "@media (prefers-color-scheme:dark){ body,.mm-bg{background:#0b0f19!important} .mm-card{background:#151b28!important}"
        " .mm-ink{color:#f3f4f6!important} .mm-muted{color:#cbd5e1!important} .mm-faint{color:#94a3b8!important} }\n"
        "</style>\n</head>\n"
        f'<body class="mm-bg" style="margin:0;padding:0;background:{_BG};font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif">\n'
        f'<div style="display:none;max-height:0;overflow:hidden">{_esc(summary)} new this week.</div>\n'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="mm-bg" '
        f'style="background:{_BG}"><tr><td align="center" class="mm-wrap" style="padding:24px 16px">\n'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px">\n'
        '<tr><td style="background:linear-gradient(135deg,#1e3a8a,#2563eb);background-color:#1e3a8a;border-radius:16px;padding:24px 24px 22px">'
        '<div style="font-size:12px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#bfdbfe">Your weekly digest</div>'
        '<div style="font-size:26px;font-weight:800;color:#ffffff;margin-top:4px">This week from MediaMate</div>'
        f'<div style="font-size:14px;color:#dbeafe;margin-top:6px">{_esc(week_label)} · {_esc(summary)}</div>'
        "</td></tr>\n"
        + "".join(parts) +
        f'\n<tr><td class="mm-faint" style="padding:28px 0 8px;text-align:center;font-size:12px;color:{_FAINT}">'
        f"{view}Sent by MediaMate · change or turn off the digest in Settings → Weekly Digest Email</td></tr>\n"
        "</table>\n</td></tr></table>\n</body>\n</html>"
    )
    return html, images


def render_text(d: dict, week_label: str, view_url: str = "") -> str:
    """The plain-text part of the email."""
    c = counts(d)
    lines = [f"This week from MediaMate · {week_label}", ""]
    hl = d.get("highlight")
    if hl:
        it = hl["item"]
        lines += [f"Highlight: {it['title']}" + (f" ({it.get('year')})" if it.get("year") else ""),
                  _clip(it.get("summary") or "", 240), it.get("plex_link") or "", ""]
    mp = d.get("mature_pick")
    if mp:
        it = mp["item"]
        lines += [f"Mature pick of the week: {it['title']}" + (f" ({it.get('year')})" if it.get("year") else ""),
                  _clip(it.get("summary") or "", 240), it.get("plex_link") or "", ""]
    for key, title, kind in _SECTION_DEFS:
        items = d["sections"].get(key, [])
        if not items:
            continue
        lines.append(f"{title} ({len(items)})")
        for i in items:
            if kind == "movie":
                lines.append(f"  • {i['title']}" + (f" ({i.get('year')})" if i.get("year") else ""))
            else:
                n = len(set(i.get("episodes", [])))
                lines.append(f"  • {i['title']}: {n} new episode{'s' if n != 1 else ''}"
                             + (f" ({i.get('episode_ranges')})" if i.get("episode_ranges") else ""))
        lines.append("")
    if d.get("music"):
        lines.append(f"New music ({len(d['music'])})")
        lines += [f"  • {a['artist']} – {a['title']}" for a in d["music"]]
        lines.append("")
    coming = d.get("coming") or {}
    if coming.get("shows") or coming.get("movies"):
        lines.append("Coming next week")
        for sh in coming.get("shows", []):
            e = sh["episodes"][0]
            lines.append(f"  • {_day(e['airs_at'])}: {sh['title']} {e['code']}" + (f" (+{len(sh['episodes']) - 1} more)" if len(sh["episodes"]) > 1 else ""))
        for m in coming.get("movies", []):
            lines.append(f"  • {_day(m['release_date'])}: {m['title']} (movie)")
        lines.append("")
    if not any([c["movies"], c["episodes"], c["albums"]]):
        lines += ["Nothing new was added this week.", ""]
    if view_url:
        lines.append(f"View in browser: {view_url}")
    lines.append("Sent by MediaMate")
    return "\n".join(lines)


# ── sending, and the "View in browser" copy ─────────────────────────────────

_SPLIT = re.compile(r"[\s,;]+")


def parse_recipients(raw: str | None) -> list[str]:
    """The recipient list as typed (commas, semicolons, spaces or new lines), each
    address once: duplicates are dropped ignoring upper and lower case."""
    seen: set[str] = set()
    out: list[str] = []
    for addr in _SPLIT.split(raw or ""):
        addr = addr.strip()
        if addr and addr.lower() not in seen:
            seen.add(addr.lower())
            out.append(addr)
    return out


def _message(from_addr: str, to: str, html: str, week_label: str, text: str,
             images: dict | None) -> str:
    """One recipient's copy: multipart/related (HTML + attached posters) inside
    multipart/alternative (plain text first), addressed to that person only."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"This week from MediaMate — {week_label}"
    msg["From"]    = from_addr
    msg["To"]      = to
    msg["Date"]    = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="mediamate.local")
    msg.attach(MIMEText(text or "Open this email in an HTML-capable mail app to see the digest.", "plain", "utf-8"))
    if images:
        related = MIMEMultipart("related")
        related.attach(MIMEText(html, "html", "utf-8"))
        for cid, data in images.items():
            img = MIMEImage(data, "jpeg")
            img.add_header("Content-ID", f"<{cid}>")
            img.add_header("Content-Disposition", "inline", filename=cid.split("@")[0] + ".jpg")
            related.attach(img)
        msg.attach(related)
    else:
        msg.attach(MIMEText(html, "html", "utf-8"))
    return msg.as_string()


def send_email(smtp_cfg: dict, recipients: list[str], html: str, week_label: str,
               text: str = "", images: dict | None = None) -> dict:
    """Send each recipient their own copy over one SMTP connection, so nobody sees
    anyone else's address. Returns {"sent": [...], "failed": {address: reason}}.
    Raises when the server can't be reached or every recipient was refused."""
    host      = smtp_cfg.get("host", "")
    port      = int(smtp_cfg.get("port") or 587)
    user      = smtp_cfg.get("user", "")
    password  = smtp_cfg.get("password", "")
    from_addr = smtp_cfg.get("from_email", "") or user
    recipients = parse_recipients(",".join(recipients))
    sent: list[str] = []
    failed: dict[str, str] = {}

    ctx = ssl.create_default_context()
    if port == 465:
        server_cm = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
    else:
        server_cm = smtplib.SMTP(host, port, timeout=30)
    with server_cm as server:
        if port != 465:
            server.ehlo()
            server.starttls(context=ctx)
            server.ehlo()
        if user and password:
            server.login(user, password)
        for to in recipients:
            try:
                server.sendmail(from_addr, [to], _message(from_addr, to, html, week_label, text, images))
                sent.append(to)
            except smtplib.SMTPServerDisconnected:
                raise
            except (smtplib.SMTPException, OSError) as e:
                failed[to] = str(e)[:200]
                logger.warning("Digest to %s failed: %s", to, failed[to])

    if recipients and not sent:
        raise RuntimeError(f"Every recipient was refused: {next(iter(failed.values()))}")
    logger.info("Weekly digest sent to %d recipient(s)%s", len(sent),
                f", {len(failed)} failed" if failed else "")
    return {"sent": sent, "failed": failed}


# ── send history (kept in the settings table, so it survives upgrades) ──────

HISTORY_KEY = "digest_history"
HISTORY_KEEP = 20


def record_send(db, kind: str, sent: int = 0, failed: dict | None = None,
                error: str | None = None) -> None:
    """Remember one send attempt: when, what kind (scheduled / manual / test),
    how many it reached, who it didn't, and any error."""
    import json
    from ..models import Setting
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind,
             "sent": sent, "failed": failed or {}, "error": error}
    row = db.query(Setting).filter(Setting.key == HISTORY_KEY).first()
    try:
        items = json.loads(row.value) if row and row.value else []
    except ValueError:
        items = []
    items = ([entry] + items)[:HISTORY_KEEP]
    if row:
        row.value = json.dumps(items)
    else:
        db.add(Setting(key=HISTORY_KEY, value=json.dumps(items)))
    db.commit()


def history(db) -> list[dict]:
    import json
    from ..models import Setting
    row = db.query(Setting).filter(Setting.key == HISTORY_KEY).first()
    try:
        return json.loads(row.value) if row and row.value else []
    except ValueError:
        return []


def _view_dir() -> Path:
    from .media import preview
    return preview.cache_dir().parent / "digest"


def save_view_copy(html_inline: str) -> str:
    """Keep the browser copy of the latest digest; returns its secret token (one at a time)."""
    folder = _view_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.html"):
        try:
            old.unlink()
        except OSError:
            pass
    token = secrets.token_urlsafe(24)
    (folder / f"{token}.html").write_text(html_inline, encoding="utf-8")
    return token


def view_copy(token: str) -> str | None:
    if not re.fullmatch(r"[A-Za-z0-9_\-]{20,64}", token or ""):
        return None
    p = _view_dir() / f"{token}.html"
    return p.read_text(encoding="utf-8") if p.is_file() else None


def public_base(db) -> str:
    from ..models import Setting
    row = db.get(Setting, "public_url")
    return (row.value if row and row.value else "").strip().rstrip("/")


def compose(db, plex_url: str, token: str, excluded_libs: frozenset[str], week_label: str,
            keep_view_copy: bool = True) -> dict:
    """Build and render the digest. Returns {html, text, images, preview} where preview
    is the same email with pictures inline (for the app and the browser copy)."""
    d = build_digest(db, plex_url, token, excluded_libs)
    preview, _ = render_html(d, week_label, inline_images=True)
    view_url = ""
    base = public_base(db)
    if keep_view_copy and base:
        view_url = f"{base}/api/public/digest/{save_view_copy(preview)}"
    html, images = render_html(d, week_label, view_url=view_url)
    return {"html": html, "text": render_text(d, week_label, view_url), "images": images,
            "preview": preview, "counts": counts(d)}
