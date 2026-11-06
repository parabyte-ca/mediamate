from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, ForeignKey, BigInteger, UniqueConstraint
from sqlalchemy.sql import func, text
from sqlalchemy.orm import relationship
from .database import Base


class WatchlistItem(Base):
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    search_query = Column(String, nullable=False)
    quality = Column(String, default="1080p")
    season = Column(Integer, default=1)
    episode = Column(Integer, default=1)
    download_path_id = Column(Integer, ForeignKey("download_paths.id"), nullable=True)
    codec = Column(String, default="x265", server_default="x265")
    enabled = Column(Boolean, default=True)
    last_checked = Column(DateTime, nullable=True)
    last_found = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    show_status = Column(String, nullable=True)
    show_status_checked_at = Column(DateTime, nullable=True)
    tvmaze_id = Column(Integer, nullable=True)
    show_status_override = Column(Boolean, default=False, server_default="0")

    # Download quality controls
    max_size_mb = Column(Integer, nullable=True)          # reject results larger than this
    preferred_groups = Column(String, nullable=True)      # comma-separated release groups, ranked
    prefer_season_packs = Column(Boolean, default=False, server_default="0")
    # Rename & organise: "general" or "mature" folders; None = decide from the age rating.
    audience = Column(String, nullable=True)

    # Optional reusable quality profile; when set it overrides the per-item
    # quality / codec / size / groups / season-pack fields above.
    profile_id = Column(Integer, ForeignKey("quality_profiles.id"), nullable=True)

    # Show metadata from TVMaze (poster, blurb, network) — cosmetic only.
    poster_url = Column(String, nullable=True)
    summary = Column(String, nullable=True)
    network = Column(String, nullable=True)
    premiered = Column(String, nullable=True)
    # Set after the first lookup attempt, successful or not, so shows TVMaze
    # does not know are not re-queried on every page load.
    metadata_checked_at = Column(DateTime, nullable=True)
    # Consecutive lookups that found no poster; drives the retry back-off.
    metadata_attempts = Column(Integer, default=0, server_default="0")

    # Movies: "tv" items follow episodes; "movie" items wait for the release date,
    # download once, and leave the watchlist some days after (unless kept).
    kind = Column(String, nullable=False, default="tv", server_default="tv")
    tmdb_id = Column(Integer, nullable=True)
    year = Column(Integer, nullable=True)
    release_date = Column(String, nullable=True)        # YYYY-MM-DD: when good releases appear
    release_kind = Column(String, nullable=True)        # digital | physical | estimated
    completed_at = Column(DateTime, nullable=True)      # the movie's download finished
    # Music: "artist" items follow an artist (MusicBrainz id); albums in music_albums.
    mbid = Column(String, nullable=True, index=True)
    keep = Column(Boolean, default=False, server_default="0")

    episodes = relationship(
        "WatchlistEpisode",
        back_populates="watchlist_item",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class QualityProfile(Base):
    """Named, reusable download preferences assignable to watchlist items."""
    __tablename__ = "quality_profiles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    quality = Column(String, nullable=False, default="1080p", server_default="1080p")
    codec = Column(String, nullable=False, default="x265", server_default="x265")
    max_size_mb = Column(Integer, nullable=True)
    preferred_groups = Column(String, nullable=True)
    prefer_season_packs = Column(Boolean, default=False, server_default="0")
    # If nothing is found at `quality` this long after an episode aired, try this.
    fallback_quality = Column(String, nullable=True)
    fallback_after_hours = Column(Integer, nullable=False, default=24, server_default="24")


class WatchlistEpisode(Base):
    """Persistent record of every episode successfully sent to qBittorrent via the watchlist.

    The UniqueConstraint on (watchlist_id, season, episode) is the authoritative
    deduplication guard — the scheduler catches IntegrityError and continues.
    ondelete="CASCADE" ensures rows are removed when the parent watchlist item
    is deleted, without requiring application-level cleanup.
    """
    __tablename__ = "watchlist_episodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    watchlist_id = Column(
        Integer,
        ForeignKey("watchlist.id", ondelete="CASCADE"),
        nullable=False,
    )
    season = Column(Integer, nullable=False)
    episode = Column(Integer, nullable=False)
    downloaded_at = Column(DateTime, nullable=False, server_default=func.now())
    torrent_hash = Column(String, nullable=True)
    torrent_name = Column(String, nullable=True)

    watchlist_item = relationship("WatchlistItem", back_populates="episodes")

    __table_args__ = (
        UniqueConstraint(
            "watchlist_id", "season", "episode",
            name="uq_watchlist_season_episode",
        ),
    )


class Download(Base):
    __tablename__ = "downloads"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    torrent_hash = Column(String, nullable=True, index=True)
    magnet_link = Column(String, nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    status = Column(String, default="adding")
    download_path = Column(String, nullable=True)
    watchlist_id = Column(Integer, ForeignKey("watchlist.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    # Timestamp of first poll where qBittorrent reported a complete state.
    # Used to enforce the 60-second grace period before auto-removal.
    completion_first_seen_at = Column(DateTime, nullable=True)
    # True once the torrent has been successfully deleted from qBittorrent.
    qbit_removed = Column(Boolean, default=False, server_default="0")
    # Alert de-duplication: each event notifies at most once per download.
    notified_complete = Column(Boolean, default=False, server_default="0")
    notified_failed = Column(Boolean, default=False, server_default="0")
    notified_stalled = Column(Boolean, default=False, server_default="0")
    # Progress tracking used for real stall detection (no progress for N hours).
    last_progress = Column(Float, nullable=True)
    last_progress_at = Column(DateTime, nullable=True)
    # How many automatic re-searches led to this download (0 = original grab).
    retry_count = Column(Integer, default=0, server_default="0")
    music_album_id = Column(Integer, ForeignKey("music_albums.id"), nullable=True)


class MusicAlbum(Base):
    """An album of an artist on the Watchlist (MusicBrainz release group).

    status: wanted (look for it once released) | grabbed (downloading) | have |
    ignored (older albums when following "new releases only", or skipped by hand).
    """
    __tablename__ = "music_albums"

    id = Column(Integer, primary_key=True, index=True)
    watchlist_id = Column(Integer, ForeignKey("watchlist.id", ondelete="CASCADE"), nullable=False, index=True)
    mbid = Column(String, nullable=False)
    title = Column(String, nullable=False)
    album_type = Column(String, nullable=True)           # Album | EP
    release_date = Column(String, nullable=True)         # YYYY[-MM[-DD]]
    status = Column(String, nullable=False, default="wanted", server_default="wanted")
    torrent_name = Column(String, nullable=True)
    quality = Column(String, nullable=True)              # flac | mp3-320 | ...
    grabbed_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (UniqueConstraint("watchlist_id", "mbid", name="uq_music_album"),)


class DownloadPath(Base):
    """A folder ScreenMate knows about. One list serves both halves of the app:

    * ``downloads``: qBittorrent may save into it; ``path`` is that folder as
      qBittorrent sees it.
    * ``scan``: the media engine scans, encodes, splits and trims files in it,
      at ``local_path`` (or, when that is empty, ``path`` translated by the path
      mappings). A library-only folder has downloads off and path == local_path.
    """
    __tablename__ = "download_paths"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    path = Column(String, nullable=False)
    is_default = Column(Boolean, default=False)
    local_path = Column(String, nullable=True)
    downloads = Column(Boolean, nullable=False, default=True, server_default=text("1"))
    scan = Column(Boolean, nullable=False, default=True, server_default=text("1"))
    # Target format for files here (services/media/targets.py): auto | tv | movies | custom.
    target = Column(String, nullable=False, default="auto", server_default="auto")
    target_min = Column(Integer, nullable=True)        # custom only: heights, e.g. 720
    target_max = Column(Integer, nullable=True)
    target_shrink = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    # Never rename or move files here (adult content, e.g. Bellesa). See services/adult.py.
    protected = Column(Boolean, nullable=False, default=False, server_default=text("0"))


class OrganiseMove(Base):
    """Every Rename & organise move, so one can be traced and undone."""
    __tablename__ = "organise_moves"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(Integer, nullable=True, index=True)
    from_path = Column(String, nullable=False)
    to_path = Column(String, nullable=False, index=True)
    moved_at = Column(DateTime, server_default=func.now())


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=True)


class DownloadHistory(Base):
    """Persistent record of every torrent sent to qBittorrent.

    Created immediately after a successful add_torrent() call (source may be
    "manual" from the Search page or "watchlist" from the auto-scanner).
    Status is updated in-place as qBittorrent reports progress.
    """
    __tablename__ = "download_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Torrent display name
    name = Column(String, nullable=False)
    # "manual" | "watchlist"
    source = Column(String, nullable=False)
    # Indexer the result came from, e.g. "nyaa", "tpb", "jackett/PublicHD"
    indexer = Column(String, nullable=True)
    # Destination folder display name (not the Linux path)
    folder = Column(String, nullable=True)
    # qBittorrent info-hash — used to match completion events
    torrent_hash = Column(String, nullable=True, index=True)
    # Total torrent size in bytes (may be populated later from qBittorrent)
    size_bytes = Column(BigInteger, nullable=True)
    # When the torrent was submitted to qBittorrent
    added_at = Column(DateTime, nullable=False, server_default=func.now())
    # Set when qBittorrent reports the torrent is seeding/completed
    completed_at = Column(DateTime, nullable=True)
    # "downloading" | "completed" | "failed"
    status = Column(String, nullable=False, default="downloading")
    # Populated when source == "watchlist"
    watchlist_id = Column(Integer, ForeignKey("watchlist.id"), nullable=True)
    # Populated when status == "failed"
    error_msg = Column(String, nullable=True)


class TraktSeen(Base):
    """Trakt watchlist entries the sync has already dealt with.

    Remembering them stops a show you deliberately removed from ScreenMate being
    re-added on the next sync.  Unmatched shows are retried after a week.
    """
    __tablename__ = "trakt_seen"

    trakt_id = Column(Integer, primary_key=True, autoincrement=False)
    title = Column(String, nullable=False)
    # "added" | "existing" | "unmatched"
    status = Column(String, nullable=False)
    tvmaze_id = Column(Integer, nullable=True)
    checked_at = Column(DateTime, nullable=False, server_default=func.now())


# ── Media engine (ported from FileSplitter) ───────────────────────────────────
# Columns mirror FileSplitter's `files` / `jobs` tables so services/media/store.py
# can keep its original SQL and the importer can copy rows straight across.
# Timestamps are ISO-8601 strings, as FileSplitter stored them.

class MediaFile(Base):
    __tablename__ = "media_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    path = Column(String, unique=True, nullable=False)
    filename = Column(String, nullable=False)
    size_bytes = Column(BigInteger)
    duration_sec = Column(Float)
    codec = Column(String)
    is_anthology = Column(Integer, default=0)
    status = Column(String, default="pending", index=True)
    error_msg = Column(String)
    discovered_at = Column(String)
    updated_at = Column(String)
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    # NULL = automatic classification; 0/1 = manual override from the UI
    anthology_override = Column(Integer, nullable=True)
    # Plex sync (services/plex_sync.py): stars 1-5 (NULL = none), collection names (JSON list),
    # Plex's item key, what Plex last said (JSON {"rating", "collections"}), unsent local edit.
    rating = Column(Integer, nullable=True)
    collections = Column(String, nullable=True)
    plex_key = Column(String, nullable=True)
    plex_seen = Column(String, nullable=True)
    plex_dirty = Column(Integer, nullable=False, default=0, server_default="0")


class MediaJob(Base):
    __tablename__ = "media_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(Integer, ForeignKey("media_files.id"), index=True)
    job_type = Column(String, nullable=False)  # encode | split | trim
    status = Column(String, default="queued", index=True)  # queued | running | done | error | cancelled
    progress_pct = Column(Float, default=0)
    target_resolution = Column(String, default="original")
    log_tail = Column(String)
    started_at = Column(String)
    finished_at = Column(String)
    saved_bytes = Column(BigInteger, default=0)
    episode_count = Column(Integer, default=0)
    journey_id = Column(Integer, ForeignKey("journeys.id"), nullable=True, index=True)
    reason = Column(String, nullable=True)  # why it was queued, e.g. "Rule: Shrink to x265"
    # For Insights' speed comparison: which encoder ran it, and how much video it processed.
    encoder = Column(String, nullable=True)  # cpu | nvenc
    media_seconds = Column(Float, nullable=True)


class Journey(Base):
    """One item's trip through ScreenMate (found → download → encode/split/trim → …).

    Steps are not stored here: they are derived from the Download row and the
    MediaJob rows pointing at this journey (services/journeys.py), so they can
    never drift from the real state. `status` is cached for cheap filtering.
    """
    __tablename__ = "journeys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String, nullable=False)
    source = Column(String, nullable=False)  # download | library
    download_id = Column(Integer, ForeignKey("downloads.id", ondelete="SET NULL"),
                         unique=True, nullable=True)
    media_file_id = Column(Integer, ForeignKey("media_files.id", ondelete="SET NULL"),
                           nullable=True, index=True)
    status = Column(String, default="active", index=True)  # active | done | failed | cancelled
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), index=True)


# ── Auto pipeline ─────────────────────────────────────────────────────────────

class PipelineRule(Base):
    """'If a file looks like this, do these steps.' First enabled match wins.

    match   — JSON: source, codecs, min/max_height, min_size_mb, min/max_duration_min,
              keywords, anthology, path_prefix, watchlist_ids (see services/pipeline.py)
    actions — JSON list, run in order: {"type": "trim"} | {"type": "encode",
              "resolution": "original"} | {"type": "split", "episode_count": 0}
    """
    __tablename__ = "pipeline_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    enabled = Column(Boolean, default=True, server_default="1")
    priority = Column(Integer, default=100)
    match = Column(String, nullable=False, default="{}")
    actions = Column(String, nullable=False, default="[]")
    created_at = Column(DateTime, server_default=func.now())


class PathMapping(Base):
    """Translate a path as qBittorrent sees it into the path inside this container."""
    __tablename__ = "path_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    remote_prefix = Column(String, nullable=False)
    local_prefix = Column(String, nullable=False)


class PipelinePlan(Base):
    """One file's planned route through a rule's actions, inside a journey."""
    __tablename__ = "pipeline_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    journey_id = Column(Integer, ForeignKey("journeys.id"), nullable=True, index=True)
    download_id = Column(Integer, nullable=True, index=True)
    file_id = Column(Integer, nullable=True, index=True)
    rule_id = Column(Integer, nullable=True)
    rule_name = Column(String, nullable=True)
    actions = Column(String, nullable=False, default="[]")
    next_index = Column(Integer, default=0)
    # waiting (for the torrent to leave qBittorrent) | running | done | failed | cancelled
    status = Column(String, default="waiting", index=True)
    detail = Column(String, nullable=True)
    source_path = Column(String, nullable=True)
    organise_status = Column(String, nullable=True)  # done | failed (Rename & organise step)
    organised_to = Column(String, nullable=True)
    library_status = Column(String, nullable=True)  # done | skipped
    notify_status = Column(String, nullable=True)   # done | skipped
    created_at = Column(DateTime, server_default=func.now())
    finished_at = Column(DateTime, nullable=True)


class ContentRating(Base):
    """Cached age rating for a show or movie (from TMDB); cert "" = none found."""
    __tablename__ = "content_ratings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String, nullable=False)          # tv | movie
    title_key = Column(String, nullable=False, index=True)
    year = Column(Integer, nullable=False, default=0)
    title = Column(String, nullable=True)
    cert = Column(String, nullable=True)
    checked_at = Column(DateTime, nullable=True)


class PushSubscription(Base):
    """A browser/phone that asked for push notifications (Web Push)."""
    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    endpoint = Column(String, unique=True, nullable=False)
    p256dh = Column(String, nullable=False)
    auth = Column(String, nullable=False)
    label = Column(String, nullable=True)  # e.g. "Chrome on Android"
    created_at = Column(DateTime, server_default=func.now())
    last_success_at = Column(DateTime, nullable=True)
    failures = Column(Integer, default=0)
