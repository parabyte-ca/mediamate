from pydantic import BaseModel, Field, field_serializer
from typing import Literal, Optional
from datetime import datetime


class TorrentResult(BaseModel):
    title: str
    size: str
    size_bytes: int
    seeds: int
    leeches: int
    magnet: str
    info_hash: Optional[str] = None
    quality: Optional[str] = None
    source: str
    url: Optional[str] = None


# ── Download Paths ────────────────────────────────────────────────────────────

class DownloadPathCreate(BaseModel):
    name: str
    path: str
    is_default: bool = False
    local_path: Optional[str] = None   # blank: worked out from the path mappings
    downloads: bool = True             # qBittorrent may save here
    scan: bool = True                  # the media engine scans and encodes here
    # Target format (all H.265); None = guessed from the name.
    target: Optional[Literal["auto", "tv", "movies", "custom", "music"]] = None
    target_min: Optional[int] = None
    target_max: Optional[int] = None
    target_shrink: Optional[bool] = None
    protected: Optional[bool] = None      # never rename or move files here; None = guessed
    role: Optional[Literal["", "tv", "movies", "mature_tv", "mature_movies", "music"]] = None
    music_downloads: Optional[bool] = None


class DownloadPathUpdate(BaseModel):
    name: Optional[str] = None
    path: Optional[str] = None
    is_default: Optional[bool] = None
    local_path: Optional[str] = None
    downloads: Optional[bool] = None
    scan: Optional[bool] = None
    target: Optional[Literal["auto", "tv", "movies", "custom", "music"]] = None
    target_min: Optional[int] = None
    target_max: Optional[int] = None
    target_shrink: Optional[bool] = None
    protected: Optional[bool] = None
    role: Optional[Literal["", "tv", "movies", "mature_tv", "mature_movies", "music"]] = None
    music_downloads: Optional[bool] = None


class DownloadPathOut(BaseModel):
    id: int
    name: str
    path: str
    is_default: bool
    local_path: Optional[str] = None
    local: str = ""                    # where the folder is here (local_path or mapped path)
    exists_here: bool = False
    downloads: bool = True
    scan: bool = True
    free_bytes: Optional[int] = None
    total_bytes: Optional[int] = None
    low_space: bool = False
    target: str = "auto"
    target_min: Optional[int] = None
    target_max: Optional[int] = None
    target_shrink: bool = False
    target_summary: str = ""           # e.g. "H.265, 720p–1080p"
    protected: bool = False            # never renamed or moved (adult content)
    role: Optional[str] = None         # library for tv | movies | mature_tv | mature_movies | music
    music_downloads: bool = False      # album downloads go here
    notes: list[str] = []              # e.g. "Same as TV Shows apart from capital letters"

    model_config = {"from_attributes": True}


# ── Watchlist ─────────────────────────────────────────────────────────────────

class WatchlistCreate(BaseModel):
    title: str
    search_query: str
    quality: str = "1080p"
    codec: str = "x265"
    season: int = 1
    episode: int = 1
    download_path_id: Optional[int] = None
    enabled: bool = True
    max_size_mb: Optional[int] = None
    preferred_groups: Optional[str] = None
    prefer_season_packs: bool = False
    profile_id: Optional[int] = None
    audience: Optional[Literal["auto", "general", "mature"]] = None   # None/"auto" = from the age rating
    tvmaze_id: Optional[int] = None    # picked from the suggestions: no guessing
    kind: Literal["tv", "movie"] = "tv"


class WatchlistUpdate(BaseModel):
    title: Optional[str] = None
    search_query: Optional[str] = None
    quality: Optional[str] = None
    codec: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    download_path_id: Optional[int] = None
    enabled: Optional[bool] = None
    max_size_mb: Optional[int] = None
    preferred_groups: Optional[str] = None
    prefer_season_packs: Optional[bool] = None
    profile_id: Optional[int] = None
    # Rename & organise folder: "auto" (from the age rating), "general" or "mature".
    audience: Optional[Literal["auto", "general", "mature"]] = None
    keep: Optional[bool] = None             # movies: stay on the watchlist after downloading


class WatchlistOut(BaseModel):
    id: int
    title: str
    search_query: str
    quality: str
    codec: str = "x265"
    season: int
    episode: int
    download_path_id: Optional[int] = None
    enabled: bool
    last_checked: Optional[datetime] = None
    last_found: Optional[datetime] = None
    created_at: datetime
    show_status: Optional[str] = None
    show_status_checked_at: Optional[datetime] = None
    tvmaze_id: Optional[int] = None
    show_status_override: bool = False
    max_size_mb: Optional[int] = None
    preferred_groups: Optional[str] = None
    prefer_season_packs: bool = False
    profile_id: Optional[int] = None
    poster_url: Optional[str] = None
    summary: Optional[str] = None
    network: Optional[str] = None
    premiered: Optional[str] = None
    metadata_checked_at: Optional[datetime] = None
    audience: Optional[str] = None
    kind: Optional[str] = "tv"
    tmdb_id: Optional[int] = None
    year: Optional[int] = None
    release_date: Optional[str] = None
    release_kind: Optional[str] = None
    completed_at: Optional[datetime] = None
    keep: Optional[bool] = False
    # Movies, filled in by GET /watchlist: waiting | looking | downloading | downloaded | paused
    movie_state: Optional[str] = None
    removes_at: Optional[datetime] = None
    # Artists: MusicBrainz id and album counts {wanted, grabbed, have, ignored, total, next}
    mbid: Optional[str] = None
    albums: Optional[dict] = None

    model_config = {"from_attributes": True}
    followers: list[int] = []          # people following it (see services/people.py)


# ── Watchlist episode tracking ────────────────────────────────────────────────

class WatchlistEpisodeOut(BaseModel):
    id: int
    watchlist_id: int
    season: int
    episode: int
    downloaded_at: datetime
    torrent_hash: Optional[str] = None
    torrent_name: Optional[str] = None

    @field_serializer("downloaded_at")
    def serialize_downloaded_at(self, v: datetime) -> str:
        return v.isoformat() + "Z"

    model_config = {"from_attributes": True}


# ── Quality profiles ──────────────────────────────────────────────────────────

QualityName = Literal["4K", "1080p", "720p", "480p", "Any"]
CodecName = Literal["x265", "x264", "AV1", "Any"]


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    quality: QualityName = "1080p"
    codec: CodecName = "x265"
    max_size_mb: Optional[int] = Field(default=None, ge=1)
    preferred_groups: Optional[str] = None
    prefer_season_packs: bool = False
    fallback_quality: Optional[QualityName] = None
    fallback_after_hours: int = Field(default=24, ge=1, le=24 * 30)


class ProfileUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    quality: Optional[QualityName] = None
    codec: Optional[CodecName] = None
    max_size_mb: Optional[int] = Field(default=None, ge=1)
    preferred_groups: Optional[str] = None
    prefer_season_packs: Optional[bool] = None
    fallback_quality: Optional[QualityName] = None
    fallback_after_hours: Optional[int] = Field(default=None, ge=1, le=24 * 30)


class ProfileOut(BaseModel):
    id: int
    name: str
    quality: str
    codec: str
    max_size_mb: Optional[int] = None
    preferred_groups: Optional[str] = None
    prefer_season_packs: bool = False
    fallback_quality: Optional[str] = None
    fallback_after_hours: int = 24
    watchlist_count: int = 0

    model_config = {"from_attributes": True}


class AddFromTmdbRequest(BaseModel):
    tmdb_id: int
    profile_id: Optional[int] = None
    audience: Optional[Literal["auto", "general", "mature"]] = None


class AddFromTvmazeRequest(BaseModel):
    tvmaze_id: int
    start: Optional[Literal["next", "beginning"]] = None
    profile_id: Optional[int] = None


class TraktTestRequest(BaseModel):
    client_id: str
    username: str


class TmdbTestRequest(BaseModel):
    api_key: str


class JackettTestRequest(BaseModel):
    url: str
    api_key: str


class MarkEpisodeRequest(BaseModel):
    season: int
    episode: int
    torrent_name: Optional[str] = None


# ── Downloads ─────────────────────────────────────────────────────────────────

class DownloadCreate(BaseModel):
    magnet: str
    title: str
    download_path_id: Optional[int] = None
    watchlist_id: Optional[int] = None
    indexer: Optional[str] = None
    force: bool = False


class DuplicateCheckRequest(BaseModel):
    torrent_hash: Optional[str] = None
    torrent_name: str


# ── Media server test connections ─────────────────────────────────────────────

class PlexTestRequest(BaseModel):
    url: str
    token: str
    library_key: Optional[str] = None


class JellyfinTestRequest(BaseModel):
    url: str
    api_key: str


class DigestTestRequest(BaseModel):
    digest_smtp_host: str = ""
    digest_smtp_port: str = "587"
    digest_smtp_user: str = ""
    digest_smtp_password: str = ""
    digest_from_email: str = ""
    digest_recipients: str = ""
    plex_url: str = ""
    plex_token: str = ""
    digest_excluded_libs: str = ""
