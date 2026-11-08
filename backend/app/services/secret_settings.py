"""Secret settings never leave the server in clear text.

GET responses carry MASK in place of a stored secret; the browser shows it in the
field and sends it back unchanged on save, which means "keep what's stored".
Test buttons send whatever is in the form, so they swap MASK for the stored value
here before calling out.
"""
import logging
import re

from sqlalchemy.orm import Session

from ..models import Setting

MASK = "••••••••"

SECRET_KEYS = frozenset({
    "qbit_password",
    "jackett_api_key",
    "apprise_url",          # Apprise URLs embed bot tokens and passwords
    "tmdb_api_key",
    "trakt_client_id",
    "plex_token",
    "jellyfin_api_key",
    "digest_smtp_password",
    "ui_password",
    "tpdb_api_key",         # scene naming (media engine)
})

# Stored server-side only; never sent to the browser at all.
SERVER_ONLY_KEYS = frozenset({
    "session_secret",
    "vapid_private_key",
})


def redact(settings: dict[str, str]) -> dict[str, str]:
    out = {k: v for k, v in settings.items() if k not in SERVER_ONLY_KEYS}
    for key in SECRET_KEYS & out.keys():
        if out[key]:
            out[key] = MASK
    return out


def is_mask(value) -> bool:
    return isinstance(value, str) and value == MASK


def resolve(db: Session, key: str, value: str) -> str:
    """The real value for a form field that may still hold MASK."""
    if not is_mask(value):
        return value
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row and row.value else ""


def scrub(text: str, *secrets: str) -> str:
    """Remove secret values from an error message before it is logged or returned."""
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, MASK)
    return scrub_urls(text)


# Query-string credentials that end up in exception text (httpx puts the full URL
# in HTTPStatusError) — X-Plex-Token, Jellyfin/Jackett/Prowlarr api keys.
_URL_SECRET = re.compile(r"(?i)\b(x-plex-token|api_?key|apikey|token|password|passkey|authkey|torrent_pass|rsskey)=([^&\s'\"]+)")


def scrub_urls(text: str) -> str:
    return _URL_SECRET.sub(lambda m: f"{m.group(1)}={MASK}", text)


def install_log_redaction() -> None:
    """Every log line, from any logger, has URL credentials masked."""
    base = logging.getLogRecordFactory()
    if getattr(base, "_screenmate_redacts", False):
        return

    def factory(*args, **kwargs):
        record = base(*args, **kwargs)
        try:
            message = record.getMessage()
        except Exception:
            return record
        clean = scrub_urls(message)
        if clean != message:
            record.msg, record.args = clean, None
        return record

    factory._screenmate_redacts = True
    logging.setLogRecordFactory(factory)
