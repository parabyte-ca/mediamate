# Parity sign-off: AuTorrent + FileSplitter → MediaMate

Every feature of the two apps MediaMate replaces, where it lives now, and what proves it works.
**Tests** are backend pytest files (`backend/tests/…`); **Smoke** is the browser run
`node frontend/tests/smoke.cjs` (28 checks: every page in light and dark, plus the flows named below).

Status as of v0.8.1: **every item is present**. One FileSplitter bug was found and fixed along the way (see the end).

## AuTorrent

| Feature | In MediaMate | Evidence |
|---|---|---|
| Search: NYAA, TPB, EZTV, YTS, TorrentGalaxy, 1337x | Search page, "All sources" or one source | `test_sources_packs_profiles.py` (TPB), `test_parity_gaps.py` (NYAA, EZTV, YTS, TorrentGalaxy, 1337x parsing) |
| Jackett / Prowlarr | Settings → Indexers, Test button | `test_sources_packs_profiles.py`, `test_secret_settings.py` (test with the saved key) |
| Adult filter, quality and codec filters | Search page filters | `test_sources_packs_profiles.py` |
| Watchlist: episodes, guide, scan, override, change match, movies | Watchlist page | `test_watchlist_episodes.py`, `test_tvmaze_features.py`, `test_tvmaze_matching.py`, `test_watchlist_movies.py` |
| TVMaze show-ended detection | Watchlist status chips | `test_tvmaze_features.py` |
| Quality profiles (incl. season packs) | Settings → Quality profiles | `test_sources_packs_profiles.py` |
| Trakt watchlist sync | Settings → Trakt | `test_trakt_onboarding.py` |
| Calendar and Browse | Calendar, Browse pages | `test_tvmaze_features.py`; Smoke (both load, both themes) |
| Downloads: qBittorrent state, stalled/retry, clear finished | Activity page (journeys) | `test_completion.py`, `test_download_events.py`, `test_download_poller.py`, `test_activity_routes.py` |
| Remove finished torrents from qBittorrent | Settings → qBittorrent | `test_parity_gaps.py` |
| Duplicate detection | Search + grab | `test_duplicate.py` |
| History: search, CSV export, delete | History page | `test_history.py` |
| Plex / Jellyfin library refresh | Settings → Media servers | `test_media_servers.py` |
| Plex weekly digest email | Settings → Weekly digest, Send test | `test_plex_digest.py` |
| Apprise notifications (+ new web push) | Settings → Notifications | `test_download_events.py`, `test_push.py` |
| TMDB movie posters, TVMaze show posters | Posters everywhere (click to enlarge) | `test_movie_posters.py`, `test_poster_lookup.py` |
| Login and "sign out everywhere" | Settings → Security | `test_parity_gaps.py`, `media/test_auth_query_token.py` |
| Setup wizard (now with import) | First run | `test_trakt_onboarding.py`, `test_importer.py` |
| Backup and restore | Settings → Data management | `test_backup.py`; Smoke (backup downloads) |
| `/health` | `GET /health` | `test_health.py`; Smoke |
| Themes and collapsible sidebar (+ five colour themes) | Sidebar footer, Settings → Appearance, Ctrl+K | Smoke (theme buttons, collapse/expand) |
| Updates page and upgrade helper | Updates page (phones: More → Updates) | `test_updates.py`, `test_update_script.py`, `test_next_version.py`; Smoke |

## FileSplitter

| Feature | In MediaMate | Evidence |
|---|---|---|
| Scan and classification (codec, duration, size, keyword, anthology) | Library → Scan | `media/test_engine_e2e.py`, `media/test_media_routes.py`, `media/test_codec_detector.py`; Smoke (scan finds the clip) |
| x265 on the CPU | Library → Encode | `media/test_engine_e2e.py`; Smoke (encode finishes as HEVC) |
| NVENC, GPU probe with retry, Recheck GPU | Settings → Media engine | `media/test_encoder.py` |
| Fall back to the CPU without a GPU | automatic | `media/test_scenes_naming_fallback.py` |
| Scene detection: select, black, freeze (title), combined, auto | Settings → Media engine | `media/test_scenes_naming_fallback.py` (every method on a real clip) |
| Split by stream copy | Library → Split | `media/test_engine_e2e.py` |
| TPDB pHash scene naming (filename fallback) | Split with a TPDB key | `media/test_scenes_naming_fallback.py` |
| Intro trim | Library → Trim | `media/test_engine_e2e.py` |
| Rename, stream preview (+ thumbnails, plays any file by converting on the fly) | Library: click a name or thumbnail; row actions | `media/test_media_routes.py`, `media/test_preview.py` |
| Delete to `.trash`, 30-day purge, Empty Trash | Library → Delete; Settings → Trash | `media/test_trash.py`, `media/test_media_routes.py` |
| Skip, cancel, pause/resume queue, clear jobs | Library, Activity | `media/test_media_routes.py`, `media/test_store.py`, `test_activity_routes.py` |
| Saved-bytes counter | Insights, status pill, Settings | `media/test_store.py`, `test_insights.py` |
| Runtime settings | Settings → Media engine | `media/test_media_config.py` |
| ffprobe timeout | Settings → Media engine | `media/test_codec_detector.py` |
| Installable app (PWA) | Install button | `frontend/tests/push-sw.test.mjs`; manifest checked in the build |
| Daily release check and in-app upgrade | Updates page | `test_updates.py`, `test_update_script.py` |

## Found and fixed during sign-off

- **Scene detection "select" never found a cut.** FileSplitter read ffmpeg's output looking for `pts_time=`, but ffmpeg writes `pts_time:6`. "Auto" mode starts with "select", so it always fell back to black-frame detection without saying so. Fixed in `services/media/scene_detector.py` and covered by `test_select_timestamps_are_read_from_ffmpeg_metadata_output`.

## Deliberately different

- AuTorrent's **Downloads** page and FileSplitter's **Active jobs** tab are now one **Activity** page; `/downloads` still redirects there.
- FileSplitter's separate login and port are gone: one app, one login, port 4260.
- Settings import from the old apps copies only settings MediaMate has, never versions, update state or repository URLs, and lets you compare before importing.
