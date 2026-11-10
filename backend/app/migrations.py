"""Lightweight schema migrations for the SQLite database.

Each ALTER is a safe no-op if the column already exists.  An optional backfill
runs only when the ALTER actually added the column, so existing rows are not
treated as brand-new events.  Called on startup and again after a backup
restore (which swaps in a database file that may predate the current schema).
"""
from sqlalchemy import text

from . import models  # noqa: F401  (registers tables on Base.metadata)
from .database import Base, engine

MIGRATIONS: list[tuple[str, str | None]] = [
    ("ALTER TABLE watchlist ADD COLUMN codec TEXT DEFAULT 'x265'", None),
    ("ALTER TABLE watchlist ADD COLUMN show_status TEXT", None),
    ("ALTER TABLE watchlist ADD COLUMN show_status_checked_at DATETIME", None),
    ("ALTER TABLE watchlist ADD COLUMN tvmaze_id INTEGER", None),
    ("ALTER TABLE watchlist ADD COLUMN show_status_override INTEGER DEFAULT 0", None),
    ("ALTER TABLE downloads ADD COLUMN completion_first_seen_at DATETIME", None),
    ("ALTER TABLE downloads ADD COLUMN qbit_removed INTEGER DEFAULT 0", None),
    ("ALTER TABLE watchlist ADD COLUMN max_size_mb INTEGER", None),
    ("ALTER TABLE watchlist ADD COLUMN preferred_groups TEXT", None),
    ("ALTER TABLE watchlist ADD COLUMN prefer_season_packs INTEGER DEFAULT 0", None),
    (
        "ALTER TABLE downloads ADD COLUMN notified_complete INTEGER DEFAULT 0",
        "UPDATE downloads SET notified_complete = 1 WHERE status IN ('completed', 'seeding', 'done')",
    ),
    (
        "ALTER TABLE downloads ADD COLUMN notified_failed INTEGER DEFAULT 0",
        "UPDATE downloads SET notified_failed = 1 WHERE status IN ('error', 'failed')",
    ),
    ("ALTER TABLE downloads ADD COLUMN notified_stalled INTEGER DEFAULT 0", None),
    ("ALTER TABLE downloads ADD COLUMN last_progress REAL", None),
    ("ALTER TABLE downloads ADD COLUMN last_progress_at DATETIME", None),
    ("ALTER TABLE downloads ADD COLUMN retry_count INTEGER DEFAULT 0", None),
    ("ALTER TABLE watchlist ADD COLUMN profile_id INTEGER", None),
    ("ALTER TABLE watchlist ADD COLUMN poster_url TEXT", None),
    ("ALTER TABLE watchlist ADD COLUMN summary TEXT", None),
    ("ALTER TABLE watchlist ADD COLUMN network TEXT", None),
    ("ALTER TABLE watchlist ADD COLUMN premiered TEXT", None),
    ("ALTER TABLE watchlist ADD COLUMN metadata_checked_at DATETIME", None),
    # Earlier versions stamped a show as "checked" even when TVMaze was merely
    # unreachable or rate-limited; clear those stamps once so they are retried.
    (
        "ALTER TABLE watchlist ADD COLUMN metadata_attempts INTEGER DEFAULT 0",
        "UPDATE watchlist SET metadata_checked_at = NULL WHERE poster_url IS NULL",
    ),
    ("ALTER TABLE media_jobs ADD COLUMN journey_id INTEGER", None),
    ("ALTER TABLE pipeline_plans ADD COLUMN organise_status TEXT", None),
    ("ALTER TABLE pipeline_plans ADD COLUMN organised_to TEXT", None),
    ("ALTER TABLE watchlist ADD COLUMN audience TEXT", None),
    ("ALTER TABLE media_jobs ADD COLUMN reason TEXT", None),
    ("ALTER TABLE media_jobs ADD COLUMN encoder TEXT", None),
    ("ALTER TABLE media_jobs ADD COLUMN media_seconds REAL", None),
    # One Folders list (download folders + media folders). The one-time merge of
    # the old media_paths setting into these rows is services/folders.merge_once().
    ("ALTER TABLE download_paths ADD COLUMN local_path TEXT", None),
    ("ALTER TABLE download_paths ADD COLUMN downloads INTEGER NOT NULL DEFAULT 1", None),
    ("ALTER TABLE download_paths ADD COLUMN scan INTEGER NOT NULL DEFAULT 1", None),
    # Movies on the watchlist. Items TMDB matched (a TMDB poster, no TVMaze show)
    # were movies all along.
    (
        "ALTER TABLE watchlist ADD COLUMN kind TEXT NOT NULL DEFAULT 'tv'",
        "UPDATE watchlist SET kind = 'movie' WHERE tvmaze_id IS NULL AND poster_url LIKE '%image.tmdb.org%'",
    ),
    ("ALTER TABLE watchlist ADD COLUMN tmdb_id INTEGER", None),
    (
        "ALTER TABLE watchlist ADD COLUMN year INTEGER",
        "UPDATE watchlist SET year = CAST(substr(premiered, 1, 4) AS INTEGER) "
        "WHERE kind = 'movie' AND premiered GLOB '[12][0-9][0-9][0-9]*'",
    ),
    ("ALTER TABLE watchlist ADD COLUMN release_date TEXT", None),
    ("ALTER TABLE watchlist ADD COLUMN release_kind TEXT", None),
    ("ALTER TABLE watchlist ADD COLUMN completed_at DATETIME", None),
    ("ALTER TABLE watchlist ADD COLUMN keep INTEGER NOT NULL DEFAULT 0", None),
    # Target formats per folder. Guess from the name: TV / Movies / Bellesa (720p).
    (
        "ALTER TABLE download_paths ADD COLUMN target TEXT NOT NULL DEFAULT 'auto'",
        "UPDATE download_paths SET target = CASE "
        "WHEN lower(name) LIKE '%bellesa%' THEN 'custom' "
        "WHEN lower(name) LIKE '%movie%' OR lower(name) LIKE '%film%' THEN 'movies' "
        "WHEN lower(name) LIKE '%tv%' OR lower(name) LIKE '%show%' OR lower(name) LIKE '%series%' THEN 'tv' "
        "ELSE 'auto' END",
    ),
    ("ALTER TABLE download_paths ADD COLUMN target_min INTEGER",
     "UPDATE download_paths SET target_min = 720 WHERE target = 'custom'"),
    ("ALTER TABLE download_paths ADD COLUMN target_max INTEGER",
     "UPDATE download_paths SET target_max = 720 WHERE target = 'custom'"),
    ("ALTER TABLE download_paths ADD COLUMN target_shrink INTEGER NOT NULL DEFAULT 0",
     "UPDATE download_paths SET target_shrink = 1 WHERE target = 'custom'"),
    # Protected folders (adult content): never renamed or moved into the Plex folders.
    (
        "ALTER TABLE download_paths ADD COLUMN protected INTEGER NOT NULL DEFAULT 0",
        "UPDATE download_paths SET protected = 1 WHERE lower(name) LIKE '%bellesa%' OR lower(name) LIKE '%adult%' "
        "OR lower(name) LIKE '%xxx%' OR lower(name) LIKE '%nsfw%' OR lower(name) LIKE '%porn%' "
        "OR lower(path) LIKE '%bellesa%' OR lower(coalesce(local_path, '')) LIKE '%bellesa%'",
    ),
    # Star ratings and collections, synced with Plex.
    ("ALTER TABLE media_files ADD COLUMN rating INTEGER", None),
    ("ALTER TABLE media_files ADD COLUMN collections TEXT", None),
    ("ALTER TABLE media_files ADD COLUMN plex_key TEXT", None),
    ("ALTER TABLE media_files ADD COLUMN plex_seen TEXT", None),
    ("ALTER TABLE media_files ADD COLUMN plex_dirty INTEGER NOT NULL DEFAULT 0", None),
    # Music: artists on the watchlist, album downloads.
    ("ALTER TABLE watchlist ADD COLUMN mbid TEXT", None),
    ("ALTER TABLE downloads ADD COLUMN music_album_id INTEGER", None),
    ("ALTER TABLE downloads ADD COLUMN error_detail VARCHAR", None),
    ("ALTER TABLE downloads ADD COLUMN replaces_album_id INTEGER", None),
    ("ALTER TABLE content_ratings ADD COLUMN genres TEXT", None),
    ("ALTER TABLE content_ratings ADD COLUMN genres_checked_at DATETIME", None),
    ("ALTER TABLE music_lib_albums ADD COLUMN cover_checked_at DATETIME", None),
    # One Folders list: library roles and the music download folder live on folders.
    ("ALTER TABLE download_paths ADD COLUMN role TEXT", None),
    ("ALTER TABLE download_paths ADD COLUMN music_downloads INTEGER NOT NULL DEFAULT 0", None),
    ("ALTER TABLE push_subscriptions ADD COLUMN person_id INTEGER", None),
    ("ALTER TABLE people ADD COLUMN avatar BLOB", None),
    ("ALTER TABLE people ADD COLUMN avatar_at DATETIME", None),
    ("ALTER TABLE share_links ADD COLUMN clip TEXT", None),
    ("ALTER TABLE music_lib_albums ADD COLUMN upgrade_dismissed_at DATETIME", None),
]


def run_migrations(bind=engine) -> None:
    Base.metadata.create_all(bind=bind)
    with bind.connect() as conn:
        for stmt, backfill in MIGRATIONS:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                continue
            if backfill:
                try:
                    conn.execute(text(backfill))
                    conn.commit()
                except Exception:
                    pass
