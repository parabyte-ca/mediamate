import logging
import xml.etree.ElementTree as ET

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Setting, WatchlistItem
from ..schemas import DigestTestRequest, TmdbTestRequest, JackettTestRequest, JellyfinTestRequest, PlexTestRequest
from ..services.indexers.jackett import probe_jackett
from ..services import tmdb
from ..services.secret_settings import SERVER_ONLY_KEYS, is_mask, redact, resolve, scrub
from ..services.qbittorrent import test_connection
from ..services.scheduler import reschedule_digest, update_interval

logger = logging.getLogger(__name__)
router = APIRouter()

DEFAULTS: dict[str, str] = {
    "qbit_host": "localhost",
    "qbit_port": "8080",
    "qbit_username": "admin",
    "qbit_password": "",
    "qbit_category": "screenmate",
    "scan_interval_minutes": "60",
    "min_seeds": "3",
    # Finished watchlist movies leave the watchlist after this many days (0 = never).
    "watchlist_movie_keep_days": "30",
    "remove_on_complete": "false",
    "jackett_url": "",
    "jackett_api_key": "",
    "apprise_url": "",
    # Movie posters (TV posters come from TVMaze, which needs no key)
    "tmdb_api_key": "",
    # Alerts (each is "true"/"false")
    "notify_on_grab": "true",
    "notify_on_complete": "true",
    "notify_on_failed": "true",
    "notify_on_stalled": "true",
    # Stall detection, retry queue and disk space
    "stall_hours": "2",
    "retry_enabled": "true",
    "retry_max_attempts": "3",
    "low_disk_warn_gb": "10",
    "default_indexer": "all",
    "default_profile_id": "",          # new TV shows
    "default_movie_profile_id": "",    # new movies
    "browse_country": "US",
    "qbit_music_category": "music",    # qBittorrent category for album downloads
    "music_download_path_id": "",      # Folders entry albums download to ("" = a folder named Music)
    # Trakt watchlist sync
    "trakt_enabled": "false",
    "trakt_client_id": "",
    "trakt_username": "",
    "trakt_start": "auto",
    # First-run wizard
    "onboarding_done": "false",
    # Plex
    "plex_enabled": "false",
    "plex_url": "",
    "plex_token": "",
    "plex_library_key": "",
    "plex_sync_enabled": "false",      # star ratings + collections, both ways
    # Jellyfin
    "jellyfin_enabled": "false",
    "jellyfin_url": "",
    "jellyfin_api_key": "",
    "jellyfin_library_id": "",
    # Weekly digest
    "digest_enabled":      "false",
    "digest_smtp_host":    "",
    "digest_smtp_port":    "587",
    "digest_smtp_user":    "",
    "digest_smtp_password": "",
    "digest_from_email":   "",
    "digest_recipients":   "",
    "digest_day_of_week":  "mon",
    "digest_hour":         "8",
    "digest_excluded_libs": "",
    # Security
    "ui_password": "",
    # Updates
    "app_source_url": "https://github.com/parabyte-ca/screenmate",
}

# Keys stored in the DB but never sent to the frontend (secrets are masked separately)
_PRIVATE_KEYS = set(SERVER_ONLY_KEYS) | {
    "session_secret",
    "last_digest_sent_at",
    "update_release_cache",
    "update_notified_version",
    "update_previous_version",
    "disk_warned",
    "trakt_last_result",
    "plex_sync_last",
}


def _load_settings(db: Session) -> dict[str, str]:
    db_settings = {
        s.key: s.value
        for s in db.query(Setting).all()
        if s.key not in _PRIVATE_KEYS
    }
    return {**DEFAULTS, **db_settings}


def _recheck_movie_posters(db: Session, key: str) -> None:
    """A newly saved TMDB key makes posterless titles worth another look right away."""
    if not tmdb.valid_key(key):
        return
    db.query(WatchlistItem).filter(
        WatchlistItem.poster_url.is_(None), WatchlistItem.tvmaze_id.is_(None),
    ).update({"metadata_checked_at": None, "metadata_attempts": 0}, synchronize_session=False)
    db.commit()


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    return redact(_load_settings(db))


@router.put("/settings")
def update_settings(updates: dict, db: Session = Depends(get_db)):
    for key, value in updates.items():
        if key in _PRIVATE_KEYS or is_mask(value):  # the mask means "keep the stored secret"
            continue
        setting = db.query(Setting).filter(Setting.key == key).first()
        str_val = str(value) if value is not None else ""
        if setting:
            setting.value = str_val
        else:
            db.add(Setting(key=key, value=str_val))
    db.commit()

    if "tmdb_api_key" in updates and not is_mask(updates["tmdb_api_key"]):
        _recheck_movie_posters(db, str(updates["tmdb_api_key"] or "").strip())

    if "scan_interval_minutes" in updates:
        try:
            update_interval(int(updates["scan_interval_minutes"]))
        except Exception:
            pass

    if "digest_day_of_week" in updates or "digest_hour" in updates:
        try:
            loaded = _load_settings(db)
            day = str(updates.get("digest_day_of_week") or loaded.get("digest_day_of_week") or "mon")
            hour_raw = updates.get("digest_hour") if "digest_hour" in updates else loaded.get("digest_hour")
            hour = int(hour_raw) if hour_raw is not None and str(hour_raw) != "" else 8
            reschedule_digest(day, hour)
        except Exception:
            pass

    return redact(_load_settings(db))


@router.post("/settings/test-tmdb")
async def test_tmdb(body: TmdbTestRequest, db: Session = Depends(get_db)):
    ok, message = await tmdb.check_key(resolve(db, "tmdb_api_key", body.api_key.strip()))
    return {"ok": ok, "message": message}


@router.post("/settings/test-qbit")
def test_qbit(db: Session = Depends(get_db)):
    success, message = test_connection()
    return {"success": success, "message": message}


@router.post("/settings/test-jackett")
def test_jackett_endpoint(body: JackettTestRequest, db: Session = Depends(get_db)):
    """Probe a Prowlarr or Jackett instance using the values currently in the form."""
    api_key = resolve(db, "jackett_api_key", body.api_key.strip())
    if not body.url.strip() or not api_key:
        return {"ok": False, "error": "Enter both the URL and the API key first."}
    result = probe_jackett(body.url.strip(), api_key)
    if isinstance(result.get("error"), str):
        result["error"] = scrub(result["error"], api_key)
    return result


@router.post("/settings/test-plex")
async def test_plex(body: PlexTestRequest, db: Session = Depends(get_db)):
    token = resolve(db, "plex_token", body.token)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{body.url}/library/sections",
                params={"X-Plex-Token": token},
            )
            if not resp.is_success:
                return {"ok": False, "error": f"HTTP {resp.status_code} — check URL and token"}
            root = ET.fromstring(resp.text)
            libraries = [
                {
                    "key": d.get("key", ""),
                    "title": d.get("title", ""),
                    "type": d.get("type", ""),
                }
                for d in root.findall(".//Directory")
                if d.get("key")
            ]
            return {"ok": True, "libraries": libraries}
    except httpx.ConnectError:
        return {"ok": False, "error": "Connection refused — check host and port"}
    except ET.ParseError as e:
        return {"ok": False, "error": f"Unexpected response format: {e}"}
    except Exception as e:
        logger.error("Plex test connection error: %s", scrub(str(e), token))
        return {"ok": False, "error": scrub(str(e), token)}


@router.post("/settings/test-digest")
def test_digest(body: DigestTestRequest, db: Session = Depends(get_db)):
    from datetime import datetime, timezone
    from ..services.plex_digest import fetch_digest_sections, render_html, send_email

    s = _load_settings(db)

    # Recipients come from the request body (current form state, not necessarily saved yet)
    recipients_raw = body.digest_recipients or ""
    recipients = [r.strip() for r in recipients_raw.replace("\n", ",").split(",") if r.strip()]
    if not recipients:
        return {"ok": False, "error": "No recipients configured"}

    plex_url   = body.plex_url or s.get("plex_url", "")
    plex_token = resolve(db, "plex_token", body.plex_token) or s.get("plex_token", "")
    if not plex_url or not plex_token:
        return {"ok": False, "error": "Plex URL and token are required — fill in the Plex section above"}

    smtp_host = body.digest_smtp_host
    if not smtp_host:
        return {"ok": False, "error": "SMTP host not configured"}

    excluded_raw = body.digest_excluded_libs or ""
    excluded_libs = frozenset(
        x.strip().lower() for x in excluded_raw.replace("\n", ",").split(",") if x.strip()
    )

    smtp_password = resolve(db, "digest_smtp_password", body.digest_smtp_password)
    try:
        sections = fetch_digest_sections(plex_url, plex_token, excluded_libs=excluded_libs)
        now = datetime.now(timezone.utc)
        week_label = f"Test — {now.strftime('%B %d, %Y')}"
        html = render_html(sections, week_label)
        smtp_cfg = {
            "host":       smtp_host,
            "port":       body.digest_smtp_port or "587",
            "user":       body.digest_smtp_user,
            "password":   smtp_password,
            "from_email": body.digest_from_email,
        }
        send_email(smtp_cfg, recipients, html, week_label)
        return {"ok": True, "message": f"Digest sent to {len(recipients)} recipient(s)"}
    except Exception as e:
        msg = scrub(str(e), plex_token, smtp_password)
        logger.error("test-digest failed: %s", msg)
        return {"ok": False, "error": msg}


@router.post("/settings/test-jellyfin")
async def test_jellyfin(body: JellyfinTestRequest, db: Session = Depends(get_db)):
    api_key = resolve(db, "jellyfin_api_key", body.api_key)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{body.url}/System/Info",
                params={"api_key": api_key},
            )
            if not resp.is_success:
                return {"ok": False, "error": f"HTTP {resp.status_code} — check URL and API key"}
            data = resp.json()
            return {
                "ok": True,
                "server_name": data.get("ServerName", ""),
                "version": data.get("Version", ""),
            }
    except httpx.ConnectError:
        return {"ok": False, "error": "Connection refused — check host and port"}
    except Exception as e:
        logger.error("Jellyfin test connection error: %s", scrub(str(e), api_key))
        return {"ok": False, "error": scrub(str(e), api_key)}
