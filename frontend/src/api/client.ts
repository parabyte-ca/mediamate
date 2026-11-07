import { authStore } from "../auth";

const BASE = "/api";

/** What the server sends instead of a stored secret; sending it back keeps the secret. */
export const SECRET_MASK = "••••••••";

function _authHeader(): Record<string, string> {
  const token = authStore.getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ..._authHeader(),
      ...(init?.headers as Record<string, string> ?? {}),
    },
  });
  if (res.status === 401) {
    authStore.clearToken();
    window.location.href = "/login";
    throw new Error("Session expired. Please log in.");
  }
  if (!res.ok) {
    // A proxy in front of MediaMate answers with HTML while the container restarts.
    const err = await res.json().catch(() => ({
      detail: res.status >= 502 && res.status <= 504
        ? `MediaMate isn't responding (HTTP ${res.status}). It may be restarting.`
        : `Request failed (HTTP ${res.status}).`,
    }));
    throw new Error(err.detail ?? err.msg ?? "Request failed");
  }
  return res.json() as Promise<T>;
}

/** An API URL with the session token in the query (for <img>, <audio>, <video>). */
function withToken(path: string, v?: string | null): string {
  const p = new URLSearchParams();
  const token = authStore.getToken();
  if (token) p.set("token", token);
  if (v) p.set("v", v);
  const q = p.toString();
  return `${BASE}${path}${q ? `?${q}` : ""}`;
}

// For endpoints that return a raw Response (file downloads, multipart uploads)
function authedFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${BASE}${path}`, {
    ...init,
    headers: { ..._authHeader(), ...(init?.headers as Record<string, string> ?? {}) },
  });
}

// ── Types ─────────────────────────────────────────────────────────────────────

export interface TorrentResult {
  title: string;
  size: string;
  size_bytes: number;
  seeds: number;
  leeches: number;
  magnet: string;
  info_hash?: string;
  quality?: string;
  source: string;
  url?: string;
}

export interface ArtistAlbums {
  wanted: number; grabbed: number; have: number; ignored: number; total: number;
  next: { title: string; date: string | null } | null;
}

export interface MusicArtist {
  mbid: string; name: string; disambiguation: string; country: string; type: string; begin: string; score: number;
}

export type AlbumStatus = "wanted" | "grabbed" | "have" | "ignored";

export interface MusicAlbum {
  id: number; mbid: string; title: string; type: string | null; release_date: string | null;
  status: AlbumStatus; released: boolean; torrent_name: string | null; quality: string | null;
  cover_url: string; completed_at: string | null;
}

export type MusicIssue = "untagged" | "missing_tracks" | "low_bitrate" | "duplicate";

export interface LibAlbum {
  id: number; folder: string; artist: string | null; title: string | null; year: number | null;
  format: string | null; tracks: number; size_bytes: number; duration_sec: number; has_cover: boolean;
  mb_release_group_id: string | null; issues: MusicIssue[]; rating: number | null; collections: string[];
  plex_dirty: number; scanned_at: string | null;
}

export interface LibTrack {
  id: number; title: string; artist: string | null; track_no: number | null; disc_no: number | null;
  format: string | null; bitrate: number | null; lossless: boolean; duration_sec: number | null;
  size_bytes: number | null; file: string;
}

export interface LibAlbumDetail extends LibAlbum { track_list: LibTrack[] }

export interface TagRow { path: string; file: string; title: string; artist: string; track_no: number | null; disc_no: number | null }
export interface AlbumTags {
  album: { album_artist: string; album: string; date: string; genre: string; comment: string };
  mixed: string[];
  tracks: TagRow[];
}
export interface NameGuess { path: string; title: string | null; track_no: number | null; disc_no: number | null; artist: string | null }
export interface MusicBatch {
  running: boolean; action: string | null; label: string | null; done: number; total: number;
  changed: number; failed: string[]; msg: string | null; finished_at: string | null;
}
export interface CoverCandidate { mbid: string; title: string; artist: string; date: string; type: string; cover: string }

export interface WatchlistItem {
  id: number;
  title: string;
  search_query: string;
  quality: string;
  codec: string;
  season: number;
  episode: number;
  download_path_id?: number;
  enabled: boolean;
  last_checked?: string;
  last_found?: string;
  created_at: string;
  show_status: "Running" | "Ended" | "To Be Determined" | "In Development" | "Unknown" | null;
  show_status_checked_at: string | null;
  tvmaze_id: number | null;
  show_status_override: boolean;
  max_size_mb?: number | null;
  preferred_groups?: string | null;
  prefer_season_packs?: boolean;
  profile_id?: number | null;
  poster_url?: string | null;
  summary?: string | null;
  network?: string | null;
  premiered?: string | null;
  metadata_checked_at?: string | null;
  /** Rename & organise folder: null = from the age rating. */
  audience?: "general" | "mature" | null;
  /** "tv" follows episodes; "movie" waits for its release and downloads once. */
  kind?: "tv" | "movie" | "artist";
  tmdb_id?: number | null;
  /** Artists: MusicBrainz id, and album counts. */
  mbid?: string | null;
  albums?: ArtistAlbums | null;
  year?: number | null;
  /** When good releases appear (YYYY-MM-DD); release_kind says how we know. */
  release_date?: string | null;
  release_kind?: "digital" | "physical" | "estimated" | null;
  completed_at?: string | null;
  /** Movies: stay on the watchlist after downloading. */
  keep?: boolean;
  movie_state?: MovieState | null;
  /** When a finished movie leaves the watchlist; null = kept or not finished. */
  removes_at?: string | null;
}

export type MovieState = "waiting" | "looking" | "downloading" | "downloaded" | "paused";

export interface QualityProfile {
  id: number;
  name: string;
  quality: string;
  codec: string;
  max_size_mb: number | null;
  preferred_groups: string | null;
  prefer_season_packs: boolean;
  fallback_quality: string | null;
  fallback_after_hours: number;
  watchlist_count: number;
}

export type ProfileInput = Omit<QualityProfile, "id" | "watchlist_count">;

export interface JackettTestResult {
  ok: boolean;
  kind?: string;
  version?: string | null;
  indexers?: number;
  error?: string;
}

export interface WatchlistCreate {
  title: string;
  search_query: string;
  quality: string;
  codec: string;
  season: number;
  episode: number;
  download_path_id?: number;
  enabled: boolean;
  max_size_mb?: number | null;
  preferred_groups?: string | null;
  prefer_season_packs?: boolean;
  profile_id?: number | null;
  audience?: "general" | "mature" | "auto" | null;
  /** Picked from the suggestions, so the show isn't guessed from the name. */
  tvmaze_id?: number | null;
  kind?: "tv" | "movie";
  keep?: boolean;
  /** Movies picked from the suggestions (added through TMDB, never guessed). */
  tmdb_id?: number | null;
}

/** A TVMaze show a watchlist item could be (Change match). */
export type ShowMatch = Omit<BrowseShow, "on_watchlist" | "watchlist_id">;

export interface Download {
  id: number;
  title: string;
  torrent_hash?: string;
  size_bytes?: number;
  status: string;
  download_path?: string;
  watchlist_id?: number;
  created_at: string;
  progress?: number;
  eta?: number;
  dlspeed?: number;
  is_stale?: boolean;
}

export interface DownloadPath {
  id: number;
  name: string;
  path: string;
  is_default: boolean;
  /** Path here when it differs and no path mapping covers it; null = worked out. */
  local_path?: string | null;
  /** Where MediaMate sees the folder (local_path or the mapped path). */
  local?: string;
  exists_here?: boolean;
  /** qBittorrent may save here. */
  downloads?: boolean;
  /** The Library scans and encodes here. */
  scan?: boolean;
  free_bytes?: number | null;
  total_bytes?: number | null;
  low_space?: boolean;
  /** Target format for files here (all H.265). */
  target?: FolderTarget;
  target_min?: number | null;
  target_max?: number | null;
  target_shrink?: boolean;
  target_summary?: string;
  /** Never rename or move files here (adult content). */
  protected?: boolean;
  /** The library this folder is for (one folder each). */
  role?: FolderRole | null;
  /** Album downloads go here. */
  music_downloads?: boolean;
  /** Things to check, e.g. a near-identical path or overlapping scans. */
  notes?: string[];
}

export type FolderRole = "tv" | "movies" | "mature_tv" | "mature_movies" | "music";

export type FolderTarget = "auto" | "tv" | "movies" | "custom" | "music";

export interface FolderInput {
  name: string;
  path: string;
  is_default: boolean;
  local_path?: string | null;
  downloads?: boolean;
  scan?: boolean;
  target?: FolderTarget;
  target_min?: number | null;
  target_max?: number | null;
  target_shrink?: boolean;
  protected?: boolean;
  role?: FolderRole | "" | null;
  music_downloads?: boolean;
}

export type Settings = Record<string, string>;

export interface PlexLibrary {
  key: string;
  title: string;
  type: string;
}

export interface PlexTestResult {
  ok: boolean;
  error?: string;
  libraries?: PlexLibrary[];
}

export interface JellyfinTestResult {
  ok: boolean;
  error?: string;
  server_name?: string;
  version?: string;
}

export interface BackupMeta {
  created_at: string;
  app_version: string;
  db_path: string;
}

export interface RestoreResponse {
  ok: boolean;
  restored_from?: string;
  message?: string;
  error?: string;
}

export interface DownloadHistoryItem {
  id: number;
  name: string;
  source: "manual" | "watchlist";
  indexer: string | null;
  folder: string | null;
  torrent_hash: string | null;
  size_bytes: number | null;
  size_human: string;
  added_at: string;        // ISO 8601 with Z suffix
  completed_at: string | null;
  status: "downloading" | "completed" | "failed";
  watchlist_id: number | null;
  error_msg: string | null;
}

export interface HistoryResponse {
  items: DownloadHistoryItem[];
  total: number;
}

export interface WatchlistEpisode {
  id: number;
  watchlist_id: number;
  season: number;
  episode: number;
  downloaded_at: string;       // ISO 8601 with Z suffix
  torrent_hash: string | null;
  torrent_name: string | null;
}

export type EpisodeStatus = "downloaded" | "missing" | "upcoming";

export interface GuideEpisode {
  number: number;
  name: string;
  airs_at: string | null;
  status: EpisodeStatus;
  torrent_name: string | null;
}

export interface GuideSeason {
  season: number;
  episodes: GuideEpisode[];
  total: number;
  aired: number;
  downloaded: number;
}

export interface EpisodeGuide {
  available: boolean;
  pointer: { season: number; episode: number };
  seasons: GuideSeason[];
}

export interface EpisodeSearchResult {
  status: "started" | "not_found" | "already_downloaded" | "duplicate";
  message: string;
  title?: string;
}

export interface CalendarEntry {
  watchlist_id: number;
  title: string;
  poster_url: string | null;
  enabled: boolean;
  season: number;
  episode: number;
  name: string;
  airs_at: string;
  status: EpisodeStatus;
}

export interface CalendarResponse {
  entries: CalendarEntry[];
  unmatched: number;
}

export type MovieList = "popular" | "now_playing" | "upcoming";

export interface BrowseMovie {
  tmdb_id: number;
  title: string;
  year: number | null;
  release_date: string | null;
  poster_url: string | null;
  overview: string | null;
  rating: number | null;
  votes: number;
  in_library: boolean;
  certification: string | null;
  on_watchlist?: boolean;
  watchlist_id?: number | null;
}

export interface BrowseShow {
  tvmaze_id: number;
  name: string;
  poster_url: string | null;
  network: string | null;
  premiered: string | null;
  status: string | null;
  genres: string[];
  rating: number | null;
  summary: string | null;
  next_airs: string | null;
  next_episode: string | null;
  season_premiere: boolean;
  on_watchlist: boolean;
  watchlist_id: number | null;
}

export interface BrowseResponse {
  mode: "airing" | "search";
  country: string;
  shows: BrowseShow[];
}

export interface TraktResult {
  ok: boolean;
  error: string | null;
  at: string;
  checked: number;
  added: string[];
  existing: number;
  unmatched: string[];
  deferred: number;
}

export interface TraktStatus {
  configured: boolean;
  enabled: boolean;
  last_result: TraktResult | null;
  seen_count: number;
  max_new_per_run: number;
}

export interface MarkEpisodeRequest {
  season: number;
  episode: number;
  torrent_name?: string;
}

export interface DuplicateCheckResult {
  is_duplicate: boolean;
  match_type: "hash" | "name" | "active" | null;
  matched_name: string | null;
  matched_at: string | null;
}

export interface LatestRelease {
  version: string;
  name: string;
  notes: string;
  url: string;
  published_at: string;
}

export interface HelperInfo {
  configured: boolean;
  connected: boolean;
  last_seen: string | null;
  every_minutes: number;
  /** An earlier run is still working (an upgrade in progress). */
  busy?: boolean;
}

export type UpgradePhase = "requested" | "started" | "downloading" | "restarting" | "done" | "failed";

export interface UpdateStatus {
  current_version: string;
  latest_release: LatestRelease | null;
  update_available: boolean;
  checked_at: string | null;
  check_error: string | null;
  helper: HelperInfo;
  upgrade_state: "requested" | "running" | "succeeded" | "failed" | null;
  upgrade_message: string | null;
  /** How far a running upgrade has got (from the helper's log). */
  upgrade_phase: UpgradePhase | null;
  can_upgrade: boolean;
  previous_version: string | null;
  can_rollback: boolean;
  helper_install_cmd: string;
  /** Median seconds of recent upgrades here; null until one has finished. */
  upgrade_estimate_seconds?: number | null;
  /** How many recent upgrades the estimate is based on. */
  upgrade_history_count?: number;
  upgrade_log?: string;
}

// ── API client ────────────────────────────────────────────────────────────────

export type StepStatus = "pending" | "running" | "done" | "failed" | "cancelled" | "skipped";

export interface JourneyStep {
  kind: "found" | "download" | "encode" | "split" | "trim" | string;
  label: string;
  status: StepStatus;
  progress?: number;
  speed?: number | null;
  eta?: number | null;
  detail?: string | null;
  log?: string | null;
  stale?: boolean;
  size_bytes?: number | null;
  saved_bytes?: number;
  path?: string | null;
  retry_count?: number;
  reason?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  ref: { type: "download" | "media_job" | "plan"; id: number };
}

export interface Journey {
  id: number;
  title: string;
  source: "download" | "library";
  status: "active" | "done" | "failed" | "cancelled";
  download_id: number | null;
  media_file_id: number | null;
  poster_url: string | null;
  created_at: string | null;
  updated_at: string | null;
  current_step: number;
  saved_bytes: number;
  steps: JourneyStep[];
}

export type ActivityFilter = "all" | "active" | "failed" | "done_today";

export interface ActivityStats {
  active: number;
  failed_24h: number;
  saved_today_bytes: number;
  queue_paused: boolean;
}

export interface ProgressEvent {
  key: string;
  journey_id: number | null;
  download_id?: number;
  job_id?: number;
  progress: number;
  speed?: number | null;
  eta?: number | null;
  log?: string | null;
}

export type PipelineActionType = "trim" | "encode" | "split" | "organise";

export type DoctorKind = "sample" | "junk" | "empty" | "broken" | "missing" | "duplicate" | "audience" | "name"
  | "oversize" | "lowres" | "adult_in_plex";

export interface OrganisePlan {
  ok: boolean;
  target?: string;
  kind?: string;
  already?: boolean;
  msg?: string;
  audience?: "general" | "mature";
  reason?: string;
  mature_folders?: boolean;
}

export interface DoctorIssue {
  id: string;
  kind: DoctorKind;
  label: string;
  path: string;
  detail: string;
  fix: string;
  safe: boolean;
  keep?: string;
  target?: string;
  result?: string;
  error?: string;
}

export interface DoctorState {
  auto_fix: boolean;
  report: { checked_at: string; issues: DoctorIssue[]; total: number; fixed: DoctorIssue[] } | null;
}

export interface OrganiseSettings {
  organise_tv_dir: string;
  organise_movie_dir: string;
  organise_tv_mature_dir: string;
  organise_movie_mature_dir: string;
  ratings_need_key?: boolean;
  organise_tv_template: string;
  organise_movie_template: string;
  warnings?: Partial<Record<"organise_tv_dir" | "organise_movie_dir" | "organise_tv_mature_dir" | "organise_movie_mature_dir", string>>;
}

export interface PipelineAction {
  type: PipelineActionType;
  resolution?: string;
  episode_count?: number;
}

export interface PipelineMatch {
  source?: "any" | "download" | "scan";
  codecs?: string[];
  min_height?: number | null;
  max_height?: number | null;
  min_size_mb?: number | null;
  min_duration_min?: number | null;
  max_duration_min?: number | null;
  keywords?: string[];
  anthology?: boolean | null;
  path_prefix?: string | null;
  watchlist_ids?: number[];
}

export interface PipelineRule {
  id: number;
  name: string;
  enabled: boolean;
  priority: number;
  match: PipelineMatch;
  actions: PipelineAction[];
}

export type PipelineRuleInput = Omit<PipelineRule, "id" | "priority"> & { priority?: number };

export interface PipelineTemplate {
  name: string;
  description: string;
  match: PipelineMatch;
  actions: PipelineAction[];
}

export interface PipelineSettings {
  pipeline_enabled: boolean;
  pipeline_on_scan: boolean;
  pipeline_remove_torrent: boolean;
}

export interface PathMappingRow {
  id?: number;
  remote_prefix: string;
  local_prefix: string;
}

export interface PipelinePreviewItem {
  file_id: number;
  filename: string;
  path: string;
  codec: string | null;
  height: number | null;
  size_bytes: number;
  rule_id: number | null;
  rule_name: string;
  actions: PipelineActionType[];
  est_saved_bytes: number;
}

export interface PipelinePreview {
  count: number;
  total_size_bytes: number;
  est_saved_bytes: number;
  encode_ratio: number;
  items: PipelinePreviewItem[];
}

export interface PathTestResult {
  path: string;
  mapped: string;
  exists: boolean;
  video_files: number;
  files: { path: string; filename?: string; codec?: string; height?: number; size_bytes?: number;
           rule_name?: string | null; actions?: string[]; error?: string }[];
}

export type MediaStatus = "pending" | "ok" | "queued" | "processing" | "done" | "error" | "skipped" | "cancelled";
export type MediaJobType = "encode" | "split" | "trim";

export interface FolderCheck {
  key: "path" | "visible" | "read" | "write" | "space" | "qbit";
  label: string;
  status: "ok" | "warn" | "fail" | "skip";
  detail: string;
}

export interface FolderSuggestion {
  path: string;          // as qBittorrent sees it
  name: string;
  why: string[];
  local: string;         // where MediaMate would find it
  visible_here: boolean;
  how: "same path" | "path mapping" | "found by name";
}

export interface FolderTest {
  ok: boolean;
  local: string;
  checks: FolderCheck[];
  hint: {
    kind: "mount" | "mapping" | "permissions" | "qbit" | "same-as-qbit";
    text: string;
    steps?: { label: string; copy: string }[];   // copyable commands or lines
  } | null;
}

export interface MediaFile {
  id: number;
  path: string;
  filename: string;
  size_bytes: number | null;
  duration_sec: number | null;
  codec: string | null;
  width: number | null;
  height: number | null;
  is_anthology: number;
  anthology_override: number | null;
  status: MediaStatus;
  error_msg: string | null;
  category: string;
  updated_at: string | null;
  /** The folder's target, e.g. "H.265, 720p (bellesa)". */
  target?: string | null;
  /** "above": H.265 but bigger than the target; "below": lower than preferred. */
  target_note?: "above" | "below" | null;
  /** Stars 1-5 (null: not rated), synced with Plex when that's on. */
  rating?: number | null;
  /** Plex collections (an episode's are its show's). */
  collections?: string[];
  plex_key?: string | null;
  /** 1: an edit not yet sent to Plex. */
  plex_dirty?: number;
}

export interface TagResult {
  ok: boolean; rating: number | null; collections: string[];
  plex: "sent" | "queued" | "off" | "local"; msg: string;
}

export interface PlexSyncStatus {
  enabled: boolean;
  pending: number;
  last: { ok: boolean; at: string; msg: string; matched?: number; unmatched?: number; pulled?: number;
          pushed?: number; skipped?: number; errors?: string[] } | null;
}

export interface MediaStats {
  total: number; done: number; processing: number; queued: number; errors: number;
  skipped: number; pending: number; ok?: number; total_saved_bytes: number; paused: boolean; scan_running: boolean;
}

export interface MediaSettings {
  media_paths: string[];
  scene_threshold: number;
  min_scene_duration: number;
  scene_method: string;
  x265_crf: number;
  x265_preset: string;
  target_resolution: string;
  encoder_backend: "cpu" | "nvenc";
  max_workers: number;
  ffprobe_timeout: number;
  black_min_duration: number;
  black_pix_th: number;
  split_min_duration: number;
  split_min_size: number;
  split_keywords: string;
  track_cleanup: string;
  keep_audio_langs: string;
  keep_sub_langs: string;
  split_scene_method: string;
  split_min_episode_gap: number;
  split_black_min_duration: number;
  split_freeze_min_duration: number;
  split_episode_count: number;
  tpdb_api_key_set: boolean;
  resolutions: string[];
  nvenc_available: boolean;
  nvenc_probe_done: boolean;
  nvenc_last_error: string | null;
  cpu_presets: string[];
  nvenc_presets: string[];
}

type MediaResult = { ok: boolean; msg?: string };

export interface ImportInspect {
  filename: string;
  token?: string;
  kind?: "autorrent" | "filesplitter";
  counts?: Record<string, number>;
  sample_paths?: string[];
  error?: string;
}

export interface ImportRunResult {
  ok: boolean;
  backup: string | null;
  results: { token: string; kind: "autorrent" | "filesplitter"; imported: Record<string, number> }[];
  mappings_added?: number;
}

/** A prefix change found by looking for the old paths' tails on this server. */
export interface PathSuggestion {
  from: string;
  to: string;
  matched: number;
  checked: number;
  via?: "filesplitter";
}

export interface SettingDifference {
  key: string;
  label: string;
  source: "autorrent" | "filesplitter";
  here: string;
  incoming: string;
  secret: boolean;
  set_here: boolean;
  import_by_default: boolean;
}

export interface SettingsComparison {
  differences: SettingDifference[];
  same: number;
}

export interface ImportPathSuggestions {
  rewrite: PathSuggestion | null;
  files_checked: number;
  mappings: PathSuggestion[];
  folders_checked: number;
  folders_missing: number;
}

export interface PushConfig {
  public_key: string;
  devices: { id: number; label: string | null; created_at: string | null }[];
}

export interface Insights {
  days: number;
  totals: {
    saved_bytes: number; saved_bytes_period: number; encodes_period: number; downloads_period: number;
    downloaded_bytes_period: number; library_files: number; library_bytes: number; hevc_share: number;
  };
  savings_by_day: { date: string; saved_bytes: number; encodes: number }[];
  downloads_by_day: { date: string; count: number; bytes: number }[];
  codec_mix: { codec: string; label: string; files: number; bytes: number }[];
  resolution_mix: { label: string; files: number; bytes: number }[];
  speed: { encoder: string; label: string; jobs: number; realtime_x: number }[];
  forecast: { files: number; bytes: number; est_saved_bytes: number; ratio: number; encoder: string; est_hours: number | null };
  top_savings: { filename: string; saved_bytes: number; after_bytes: number | null; finished_at: string | null }[];
}

export const api = {
  search(q: string, indexer = "all", quality?: string, codec = "x265", filterAdult = true) {
    const p = new URLSearchParams({ q, indexer, codec, filter_adult: String(filterAdult) });
    if (quality && quality !== "Any") p.set("quality", quality);
    return req<TorrentResult[]>(`/search?${p}`);
  },

  music: {
    searchArtists: (q: string) => req<MusicArtist[]>(`/music/artists/search?q=${encodeURIComponent(q)}`),
    follow: (body: { mbid: string; start: "new" | "latest" | "all"; include_eps?: boolean; download_path_id?: number | null }) =>
      req<WatchlistItem>("/watchlist/from-musicbrainz", { method: "POST", body: JSON.stringify(body) }),
    albums: (itemId: number) => req<MusicAlbum[]>(`/watchlist/${itemId}/albums`),
    refreshAlbums: (itemId: number) => req<{ new: number; albums: ArtistAlbums }>(`/watchlist/${itemId}/albums/refresh`, { method: "POST" }),
    setAlbum: (albumId: number, status: "wanted" | "ignored" | "have") =>
      req<MusicAlbum>(`/music/albums/${albumId}`, { method: "PATCH", body: JSON.stringify({ status }) }),
    searchAlbum: (albumId: number) =>
      req<{ status: string; message: string }>(`/music/albums/${albumId}/search`, { method: "POST" }),
    library: () => req<LibAlbum[]>("/music/library/albums"),
    libraryAlbum: (id: number) => req<LibAlbumDetail>(`/music/library/albums/${id}`),
    libraryScan: () => req<{ ok: boolean; running: boolean }>("/music/library/scan", { method: "POST" }),
    libraryStatus: () => req<{ running: boolean; roots: string[]; music_dir: string | null;
                               last: { ok: boolean; albums?: number; tracks?: number; removed?: number; msg?: string } | null }>("/music/library/status"),
    fixTags: (id: number) => req<{ ok: boolean; tagged: number; msg: string }>(`/music/library/albums/${id}/fix-tags`, { method: "POST" }),
    tags: (id: number) => req<AlbumTags>(`/music/library/albums/${id}/tags`),
    fromNames: (id: number) => req<NameGuess[]>(`/music/library/albums/${id}/from-names`),
    saveTags: (id: number, body: { album: Partial<AlbumTags["album"]>; tracks: Partial<TagRow>[];
                                   live?: { date: string; venue: string; city: string } | null }) =>
      req<{ ok: boolean; saved: number; failed: string[]; msg: string }>(`/music/library/albums/${id}/tags`, {
        method: "PUT", body: JSON.stringify(body) }),
    uploadCover: async (id: number, file: File, embed: boolean): Promise<{ ok: boolean; msg: string }> => {
      const form = new FormData();
      form.append("file", file);
      form.append("embed", embed ? "true" : "false");
      const res = await authedFetch(`/music/library/albums/${id}/cover/upload`, { method: "POST", body: form });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail ?? "Upload failed");
      return body;
    },
    coverCandidates: (id: number, q?: string) =>
      req<CoverCandidate[]>(`/music/library/albums/${id}/cover/candidates${q ? `?q=${encodeURIComponent(q)}` : ""}`),
    coverFromMusicBrainz: (id: number, mbid: string, embed: boolean) =>
      req<{ ok: boolean; msg: string }>(`/music/library/albums/${id}/cover/musicbrainz`, {
        method: "POST", body: JSON.stringify({ mbid, embed }) }),
    embedCover: (id: number) => req<{ ok: boolean; msg: string }>(`/music/library/albums/${id}/cover/embed`, { method: "POST" }),
    bulk: (body: { action: "covers" | "fix_tags" | "organise" | "embed" | "rate" | "collect"; album_ids: number[];
                   stars?: number; add?: string[]; remove?: string[] }) =>
      req<MusicBatch & { done: number; sent?: number; errors?: string[]; ok?: boolean }>("/music/library/bulk", {
        method: "POST", body: JSON.stringify(body) }),
    bulkStatus: () => req<MusicBatch>("/music/library/bulk/status"),
    getCover: (id: number) => req<{ ok: boolean; source: string | null; msg: string }>(`/music/library/albums/${id}/cover`, { method: "POST" }),
    organise: (id: number) => req<{ ok: boolean; folder: string; msg: string }>(`/music/library/albums/${id}/organise`, { method: "POST" }),
    rateAlbum: (id: number, stars: number) =>
      req<TagResult>(`/music/library/albums/${id}/rating`, { method: "POST", body: JSON.stringify({ stars }) }),
    albumCollections: (id: number, add: string[], remove: string[]) =>
      req<TagResult>(`/music/library/albums/${id}/collections`, { method: "POST", body: JSON.stringify({ add, remove }) }),
    collectionNames: () => req<{ names: string[] }>("/music/library/collections"),
    /** URLs for <img>/<audio>; they carry the token because those can't send headers. */
    coverUrl: (id: number, v?: string | null) => withToken(`/music/library/albums/${id}/cover`, v),
    trackUrl: (id: number) => withToken(`/music/library/tracks/${id}/stream`),
  },

  watchlist: {
    list: () => req<WatchlistItem[]>("/watchlist"),
    create: (data: WatchlistCreate) =>
      req<WatchlistItem>("/watchlist", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<WatchlistCreate>) =>
      req<WatchlistItem>(`/watchlist/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    delete: (id: number) => req(`/watchlist/${id}`, { method: "DELETE" }),
    scan: (id: number) => req(`/watchlist/${id}/scan`, { method: "POST" }),
    scanAll: () => req("/scan", { method: "POST" }),
    episodes: {
      list: (id: number) =>
        req<WatchlistEpisode[]>(`/watchlist/${id}/episodes`),
      mark: (id: number, data: MarkEpisodeRequest) =>
        req<WatchlistEpisode>(`/watchlist/${id}/episodes`, {
          method: "POST",
          body: JSON.stringify(data),
        }),
      deleteOne: (watchlistId: number, episodeId: number) =>
        req(`/watchlist/${watchlistId}/episodes/${episodeId}`, { method: "DELETE" }),
      reset: (id: number) =>
        req<{ deleted: number }>(`/watchlist/${id}/episodes?confirm=true`, { method: "DELETE" }),
    },
    guide: (id: number) => req<EpisodeGuide>(`/watchlist/${id}/guide`),
    searchEpisode: (id: number, season: number, episode: number) =>
      req<EpisodeSearchResult>(`/watchlist/${id}/episodes/${season}/${episode}/search`, { method: "POST" }),
    refreshMetadata: () =>
      req<{ checked: number; matched: number; remaining: number; unavailable: boolean }>("/watchlist/refresh-metadata", { method: "POST" }),
    matches: (id: number, q?: string) =>
      req<{ query: string; current: number | null; shows: ShowMatch[] }>(
        `/watchlist/${id}/matches${q ? `?q=${encodeURIComponent(q)}` : ""}`),
    setMatch: (id: number, tvmaze_id: number) =>
      req<WatchlistItem>(`/watchlist/${id}/match`, { method: "POST", body: JSON.stringify({ tvmaze_id }) }),
    addFromTmdb: (data: { tmdb_id: number; profile_id?: number | null; audience?: "auto" | "general" | "mature" | null }) =>
      req<WatchlistItem>("/watchlist/from-tmdb", { method: "POST", body: JSON.stringify(data) }),
    searchMovie: (id: number) =>
      req<{ status: string; message: string; title?: string }>(`/watchlist/${id}/search-movie`, { method: "POST" }),
    addFromTvmaze: (data: { tvmaze_id: number; start?: "next" | "beginning" }) =>
      req<WatchlistItem>("/watchlist/from-tvmaze", { method: "POST", body: JSON.stringify(data) }),
    checkShowStatuses: () =>
      req<{ ok: boolean; message: string }>("/watchlist/check-show-statuses", { method: "POST" }),
    setOverride: (id: number) =>
      req<WatchlistItem>(`/watchlist/${id}/override-status`, { method: "POST" }),
    removeOverride: (id: number) =>
      req<WatchlistItem>(`/watchlist/${id}/override-status`, { method: "DELETE" }),
  },

  downloads: {
    list: () => req<Download[]>("/downloads"),
    add: (data: { magnet: string; title: string; download_path_id?: number; indexer?: string; force?: boolean }) =>
      req("/downloads", { method: "POST", body: JSON.stringify(data) }),
    delete: (id: number) => req(`/downloads/${id}`, { method: "DELETE" }),
    clearFinished: () => req<{ ok: boolean; deleted: number }>("/downloads", { method: "DELETE" }),
    checkDuplicate: (data: { torrent_hash: string | null; torrent_name: string }) =>
      req<DuplicateCheckResult>("/downloads/check-duplicate", { method: "POST", body: JSON.stringify(data) }),
  },

  activity: {
    list: (filter: ActivityFilter = "all", limit = 50, offset = 0) =>
      req<{ total: number; items: Journey[] }>(`/activity?filter=${filter}&limit=${limit}&offset=${offset}`),
    get: (id: number) => req<Journey>(`/activity/${id}`),
    stats: () => req<ActivityStats>("/activity/stats"),
    cancel: (id: number) => req<{ ok: boolean; msg: string }>(`/activity/${id}/cancel`, { method: "POST" }),
    retry: (id: number) => req<{ ok: boolean; msg: string }>(`/activity/${id}/retry`, { method: "POST" }),
    remove: (id: number) => req<{ ok: boolean }>(`/activity/${id}`, { method: "DELETE" }),
    clearFinished: () => req<{ ok: boolean; cleared: number }>("/activity/clear-finished", { method: "POST" }),
  },

  media: {
    files: () => req<MediaFile[]>("/media/files"),
    stats: () => req<MediaStats>("/media/stats"),
    scan: () => req<MediaResult>("/media/scan", { method: "POST" }),
    queue: (fileId: number, jobType: MediaJobType, targetResolution = "original", episodeCount = 0) =>
      req<MediaResult & { job_id: number }>("/media/queue", {
        method: "POST",
        body: JSON.stringify({ file_id: fileId, job_type: jobType, target_resolution: targetResolution, episode_count: episodeCount }),
      }),
    queueBulk: (fileIds: number[], jobType: MediaJobType, targetResolution = "original", episodeCount = 0) =>
      req<{ ok: boolean; queued: number; skipped: number }>("/media/queue/bulk", {
        method: "POST",
        body: JSON.stringify({ file_ids: fileIds, job_type: jobType, target_resolution: targetResolution, episode_count: episodeCount }),
      }),
    /** Where Rename & organise would put a file (no changes). */
    organisePreview: (id: number, audience?: "general" | "mature") =>
      req<OrganisePlan>(`/media/files/${id}/organise${audience ? `?audience=${audience}` : ""}`),
    organise: (id: number) =>
      req<{ ok: boolean; msg: string; path: string }>(`/media/files/${id}/organise`, { method: "POST" }),
    bulk: (action: "skip" | "delete" | "organise" | "move", fileIds: number[],
           audiences?: Record<number, "general" | "mature">, folderId?: number) =>
      req<{ ok: boolean; done: number; skipped: number; errors: string[] }>("/media/files/bulk", {
        method: "POST", body: JSON.stringify({ action, file_ids: fileIds, audiences, folder_id: folderId }),
      }),
    rate: (id: number, stars: number) =>
      req<TagResult>(`/media/files/${id}/rating`, { method: "POST", body: JSON.stringify({ stars }) }),
    setCollections: (id: number, add: string[], remove: string[]) =>
      req<TagResult>(`/media/files/${id}/collections`, { method: "POST", body: JSON.stringify({ add, remove }) }),
    tagBulk: (body: { action: "rate"; stars: number } | { action: "collect"; add: string[]; remove: string[] }, fileIds: number[]) =>
      req<{ ok: boolean; done: number; errors: string[]; sent: number }>("/media/files/bulk", {
        method: "POST", body: JSON.stringify({ ...body, file_ids: fileIds }),
      }),
    collectionNames: () => req<{ names: string[] }>("/media/plex/collections"),
    plexStatus: () => req<PlexSyncStatus>("/media/plex/status"),
    plexSync: () => req<PlexSyncStatus["last"] & { ok: boolean; msg: string }>("/media/plex/sync", { method: "POST" }),
    rename: (id: number, newName: string) =>
      req<MediaResult & { filename?: string }>(`/media/files/${id}/rename`, { method: "POST", body: JSON.stringify({ new_name: newName }) }),
    remove: (id: number) => req<MediaResult>(`/media/files/${id}/delete`, { method: "POST" }),
    skip: (id: number) => req<MediaResult>(`/media/files/${id}/skip`, { method: "POST" }),
    cancel: (id: number) => req<MediaResult>(`/media/files/${id}/cancel`, { method: "POST" }),
    setAnthology: (id: number, value: boolean | null) =>
      req<MediaResult>(`/media/files/${id}/anthology`, { method: "POST", body: JSON.stringify({ value }) }),
    purge: () => req<{ ok: boolean; removed: number }>("/media/files/purge", { method: "POST" }),
    pause: () => req<{ ok: boolean; paused: boolean }>("/media/queue/pause", { method: "POST" }),
    resume: () => req<{ ok: boolean; paused: boolean }>("/media/queue/resume", { method: "POST" }),
    /** URL for <video src> / download links; carries the token because media elements can't send headers. */
    streamUrl: (id: number, download = false) => {
      const p = new URLSearchParams();
      const token = authStore.getToken();
      if (token) p.set("token", token);
      if (download) p.set("download", "1");
      const q = p.toString();
      return `${BASE}/media/files/${id}/stream${q ? `?${q}` : ""}`;
    },
    /** A still frame from the file (cached on the server). `v` busts the browser cache when the file changes. */
    thumbUrl: (id: number, v?: string | null) => {
      const p = new URLSearchParams();
      const token = authStore.getToken();
      if (token) p.set("token", token);
      if (v) p.set("v", v);
      const q = p.toString();
      return `${BASE}/media/files/${id}/thumb${q ? `?${q}` : ""}`;
    },
    /** The file converted on the fly to MP4 any browser plays, from `start` seconds. */
    playUrl: (id: number, start = 0) => {
      const p = new URLSearchParams();
      const token = authStore.getToken();
      if (token) p.set("token", token);
      if (start > 0) p.set("start", String(Math.floor(start)));
      const q = p.toString();
      return `${BASE}/media/files/${id}/play${q ? `?${q}` : ""}`;
    },
    settings: () => req<MediaSettings>("/media/settings"),
    saveSettings: (data: Record<string, unknown>) =>
      req<MediaResult & { rejected?: string[] }>("/media/settings", { method: "POST", body: JSON.stringify(data) }),
    recheckGpu: () => req<{ nvenc_available: boolean; nvenc_last_error: string | null }>("/media/encoder/recheck-gpu", { method: "POST" }),
    setSavedBytes: (value: number) =>
      req<{ ok: boolean; total_saved_bytes: number }>("/media/settings/saved-bytes", { method: "POST", body: JSON.stringify({ value }) }),
    trash: () => req<{ size_bytes: number; file_count: number }>("/media/trash"),
    emptyTrash: () => req<{ ok: boolean; freed_bytes: number; removed_files: number }>("/media/trash/empty", { method: "POST" }),
  },

  insights: (days = 30) => req<Insights>(`/insights?days=${days}`),

  push: {
    config: () => req<PushConfig>("/push/config"),
    subscribe: (subscription: PushSubscriptionJSON, label: string) =>
      req<{ ok: boolean; id: number }>("/push/subscribe", { method: "POST", body: JSON.stringify({ subscription, label }) }),
    unsubscribe: (endpoint: string) =>
      req<{ ok: boolean }>("/push/unsubscribe", { method: "POST", body: JSON.stringify({ endpoint }) }),
    removeDevice: (id: number) => req<{ ok: boolean }>(`/push/devices/${id}`, { method: "DELETE" }),
    test: () => req<{ ok: boolean; sent: number }>("/push/test", { method: "POST" }),
  },

  importer: {
    inspect: async (files: File[]): Promise<ImportInspect[]> => {
      const form = new FormData();
      files.forEach((f) => form.append("files", f));
      const res = await authedFetch("/import/inspect", { method: "POST", body: form });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? "Upload failed");
      return res.json();
    },
    compareSettings: (tokens: string[]) =>
      req<SettingsComparison>("/import/compare-settings", { method: "POST", body: JSON.stringify({ tokens }) }),
    suggestPaths: (tokens: string[]) =>
      req<ImportPathSuggestions>("/import/suggest-paths", { method: "POST", body: JSON.stringify({ tokens }) }),
    run: (tokens: string[], opts: {
      overwrite_settings?: boolean;
      path_rewrite?: { from: string; to: string };
      path_mappings?: { from: string; to: string }[];
      settings_choices?: Record<string, boolean>;
    } = {}) =>
      req<ImportRunResult>("/import/run", { method: "POST", body: JSON.stringify({ tokens, ...opts }) }),
  },

  pipeline: {
    settings: () => req<PipelineSettings>("/pipeline/settings"),
    saveSettings: (data: Partial<PipelineSettings>) =>
      req<PipelineSettings>("/pipeline/settings", { method: "PUT", body: JSON.stringify(data) }),
    templates: () => req<PipelineTemplate[]>("/pipeline/templates"),
    rules: () => req<PipelineRule[]>("/pipeline/rules"),
    createRule: (data: PipelineRuleInput) =>
      req<PipelineRule>("/pipeline/rules", { method: "POST", body: JSON.stringify(data) }),
    updateRule: (id: number, data: PipelineRuleInput) =>
      req<PipelineRule>(`/pipeline/rules/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    deleteRule: (id: number) => req(`/pipeline/rules/${id}`, { method: "DELETE" }),
    reorder: (ids: number[]) =>
      req<PipelineRule[]>("/pipeline/rules/reorder", { method: "POST", body: JSON.stringify({ ids }) }),
    mappings: () => req<PathMappingRow[]>("/pipeline/mappings"),
    saveMappings: (rows: PathMappingRow[]) =>
      req<PathMappingRow[]>("/pipeline/mappings", { method: "PUT", body: JSON.stringify(rows) }),
    testPath: (path: string) =>
      req<PathTestResult>("/pipeline/test-path", { method: "POST", body: JSON.stringify({ path }) }),
    preview: (rule?: Partial<PipelineRuleInput>) =>
      req<PipelinePreview>("/pipeline/preview", { method: "POST", body: JSON.stringify(rule ? { rule } : {}) }),
    organise: () => req<OrganiseSettings>("/pipeline/organise"),
    saveOrganise: (data: Partial<OrganiseSettings>) =>
      req<OrganiseSettings>("/pipeline/organise", { method: "PUT", body: JSON.stringify(data) }),
    organiseExample: (filename: string, unsaved: Partial<OrganiseSettings> = {}) =>
      req<{ ok: boolean; target?: string; error?: string; audience?: "general" | "mature"; reason?: string }>("/pipeline/organise/example", {
        method: "POST", body: JSON.stringify({ ...unsaved, filename }),
      }),
    apply: (fileIds: number[], ruleId?: number | null) =>
      req<{ ok: boolean; queued: number }>("/pipeline/apply", {
        method: "POST", body: JSON.stringify({ file_ids: fileIds, rule_id: ruleId ?? undefined }),
      }),
  },

  doctor: {
    get: () => req<DoctorState>("/doctor"),
    run: () => req<DoctorState>("/doctor/run", { method: "POST" }),
    fix: (ids: string[]) =>
      req<DoctorState & { done: number; errors: string[] }>("/doctor/fix", { method: "POST", body: JSON.stringify({ ids }) }),
    setAutoFix: (on: boolean) =>
      req<DoctorState>("/doctor/settings", { method: "PUT", body: JSON.stringify({ auto_fix: on }) }),
  },

  history: {
    list: (params: string) => req<HistoryResponse>(`/history?${params}`),
    /** Fetch the full history as a CSV file; returns a raw Response. */
    export: (): Promise<Response> => authedFetch(`/history/export`),
    deleteOne: (id: number) => req(`/history/${id}`, { method: "DELETE" }),
    clearAll: () => req<{ deleted: number }>("/history?confirm=true", { method: "DELETE" }),
  },

  paths: {
    list: () => req<DownloadPath[]>("/paths"),
    create: (data: FolderInput) =>
      req<DownloadPath>("/paths", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<FolderInput>) =>
      req<DownloadPath>(`/paths/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    delete: (id: number) => req(`/paths/${id}`, { method: "DELETE" }),
    /** Check a folder: visible, readable, writable, free space, and qBittorrent's view. */
    test: (id: number) => req<FolderTest>(`/paths/${id}/test`, { method: "POST" }),
    /** Folders qBittorrent uses that aren't in the list yet. */
    suggestions: () => req<{ connected: boolean; suggestions: FolderSuggestion[] }>("/paths/suggestions"),
  },

  trakt: {
    status: () => req<TraktStatus>("/trakt/status"),
    test: (data: { client_id: string; username: string }) =>
      req<{ ok: boolean; shows?: number; error?: string }>("/trakt/test", { method: "POST", body: JSON.stringify(data) }),
    sync: () => req<TraktResult>("/trakt/sync", { method: "POST" }),
    forgetHistory: () => req<{ ok: boolean; cleared: number }>("/trakt/seen", { method: "DELETE" }),
  },

  onboarding: {
    status: () => req<{ needed: boolean }>("/onboarding/status"),
    complete: () => req<{ ok: boolean }>("/onboarding/complete", { method: "POST" }),
  },

  calendar: {
    get: (days = 30, back = 7) => req<CalendarResponse>(`/calendar?days=${days}&back=${back}`),
  },

  browse: {
    get: (q = "") => req<BrowseResponse>(`/browse${q ? `?q=${encodeURIComponent(q)}` : ""}`),
    movies: (list: MovieList = "popular", q = "") =>
      req<{ need_key: boolean; movies: BrowseMovie[]; list: string; country?: string }>(
        `/browse/movies?list=${list}${q ? `&q=${encodeURIComponent(q)}` : ""}`),
  },

  profiles: {
    list: () => req<QualityProfile[]>("/profiles"),
    create: (data: ProfileInput) =>
      req<QualityProfile>("/profiles", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<ProfileInput>) =>
      req<QualityProfile>(`/profiles/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    delete: (id: number) =>
      req<{ ok: boolean; detached: number }>(`/profiles/${id}`, { method: "DELETE" }),
  },

  backup: {
    /** Fetch the export ZIP; returns the raw Response so the caller can stream
     *  the blob — do not pass through req<T>() which calls .json(). */
    export: (): Promise<Response> => authedFetch(`/backup/export`),
    /** Upload a backup ZIP; returns the raw Response so the caller can inspect
     *  both success JSON and error JSON without an intermediate throw. */
    restore: (file: File): Promise<Response> => {
      const form = new FormData();
      form.append("file", file);
      return authedFetch(`/backup/restore`, { method: "POST", body: form });
    },
  },

  auth: {
    status: () => req<{ auth_required: boolean }>("/auth/status"),
    login: (password: string) =>
      req<{ token: string }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ password }),
      }),
    revoke: () => req<{ ok: boolean }>("/auth/revoke", { method: "POST" }),
  },

  updates: {
    status: () => req<UpdateStatus>("/admin/updates"),
    check: () => req<UpdateStatus>("/admin/updates/check", { method: "POST" }),
    upgrade: (version: string, rollback = false) =>
      req<UpdateStatus>("/admin/updates/upgrade", {
        method: "POST",
        body: JSON.stringify({ version, rollback }),
      }),
  },

  settings: {
    get: () => req<Settings>("/settings"),
    update: (data: Partial<Settings>) =>
      req<Settings>("/settings", { method: "PUT", body: JSON.stringify(data) }),
    testQbit: () =>
      req<{ success: boolean; message: string }>("/settings/test-qbit", { method: "POST" }),
    testPlex: (data: { url: string; token: string; library_key?: string }) =>
      req<PlexTestResult>("/settings/test-plex", { method: "POST", body: JSON.stringify(data) }),
    testTmdb: (api_key: string) =>
      req<{ ok: boolean; message: string }>("/settings/test-tmdb", { method: "POST", body: JSON.stringify({ api_key }) }),
    testJackett: (data: { url: string; api_key: string }) =>
      req<JackettTestResult>("/settings/test-jackett", { method: "POST", body: JSON.stringify(data) }),
    testJellyfin: (data: { url: string; api_key: string }) =>
      req<JellyfinTestResult>("/settings/test-jellyfin", { method: "POST", body: JSON.stringify(data) }),
    testDigest: (data: {
      digest_smtp_host: string; digest_smtp_port: string; digest_smtp_user: string;
      digest_smtp_password: string; digest_from_email: string; digest_recipients: string;
      plex_url: string; plex_token: string; digest_excluded_libs: string;
    }) =>
      req<{ ok: boolean; message?: string; error?: string }>("/settings/test-digest", { method: "POST", body: JSON.stringify(data) }),
  },
};
