"""Indexer download links never reach the browser.

Jackett/Prowlarr results are often .torrent URLs carrying the indexer's API key
(``apikey=``) and, for private trackers, the tracker passkey. Search results sent to
the page carry an opaque ``mmlink:<id>`` handle instead; the real link is kept here
(7 days) and swapped back in on the server when the page asks to download it."""
import hashlib
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PREFIX = "mmlink:"
KEEP = timedelta(days=7)
_ID = re.compile(r"^mmlink:([0-9a-f]{24})$")
_SECRET_PARAMS = {"apikey", "api_key", "passkey", "torrent_pass", "authkey", "token", "key", "rsskey", "pid"}


class Expired(Exception):
    pass


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hide(db, link: str) -> str:
    """A handle for any link that isn't a magnet (magnets carry no secrets)."""
    from ..models import IndexerLink
    if not link or link.startswith("magnet:") or link.startswith(PREFIX):
        return link
    key = hashlib.sha256(link.encode()).hexdigest()[:24]
    row = db.get(IndexerLink, key)
    if row is None:
        db.add(IndexerLink(id=key, link=link, created_at=_now()))
    else:
        row.created_at = _now()
    return PREFIX + key


def reveal(db, value: str) -> str:
    """The real link behind a handle (other values are returned as they are)."""
    from ..models import IndexerLink
    m = _ID.match(value or "")
    if not m:
        return value
    row = db.get(IndexerLink, m.group(1))
    if row is None or _now() - row.created_at > KEEP:
        raise Expired("That search result has expired. Search again.")
    return row.link


def purge(db) -> int:
    from ..models import IndexerLink
    n = db.query(IndexerLink).filter(IndexerLink.created_at < _now() - KEEP).delete()
    db.commit()
    return n


def clean_url(url: str | None) -> str | None:
    """An info-page link without key-like query parameters."""
    if not url or "?" not in url:
        return url
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in _SECRET_PARAMS]
    return urlunsplit(parts._replace(query=urlencode(q)))


def for_browser(db, results: list[dict]) -> list[dict]:
    out = []
    for r in results:
        r = dict(r)
        r["magnet"] = hide(db, r.get("magnet") or "")
        r["url"] = clean_url(r.get("url"))
        out.append(r)
    db.commit()
    return out
