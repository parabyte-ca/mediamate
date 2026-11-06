# MediaMate: merging AuTorrent and FileSplitter

## Context
Two self-hosted apps cover different halves of one workflow:
- **AuTorrent** (`parabyte-ca/autorrent`): FastAPI, SQLAlchemy, APScheduler and React 18 with Vite and Tailwind. It finds media and downloads it through qBittorrent.
- **FileSplitter** (`parabyte-ca/filesplitter`): Flask, raw SQLite and a single 1,777-line `index.html`. It indexes media and re-encodes it to x265, splits anthologies, trims intros and names scenes.

Today a file leaves AuTorrent and only reaches FileSplitter on a later scan. That means two UIs, two logins, two updaters, two databases and two Docker stacks.

**MediaMate** (`parabyte-ca/screenmate`, currently empty) combines them into one app. Goals:
- full feature parity with both apps
- one streamlined UI
- an automated pipeline from finding media to a library-ready file

Decisions so far:
- Stack is FastAPI + React.
- v1 includes four extra features: Auto pipeline, Unified Live Activity, Command palette + PWA, and Savings & insights.
- Existing installs move over with a one-click importer.
- The old repos are archived once parity is reached.

## Architecture
The app is a single container with a single SQLite database (WAL mode) and a single scheduler. **AuTorrent's codebase is the base.** FileSplitter's ffmpeg engine is ported into it as services.

```
backend/app/
  routers/        autorrent routers (kept) + library, jobs, pipeline, activity, insights, import, events
  services/
    acquire/      autorrent services moved here: indexers/, qbittorrent, grab, watchlist_add, trakt*, tvmaze, tmdb, calendar_view, duplicate…
    media/        filesplitter port: codec_detector, encoder (CPU/NVENC + GPU probe), scene_detector, splitter, trimmer, scene_namer, scanner, trash
    pipeline/     rules engine, job queue/worker pool, journey tracker, path mapper
    events.py     in-process pub/sub → SSE (/api/events)
    insights.py   savings, codec-mix, throughput aggregates
    importer.py   reads autorrent.db + filesplitter.db
  models.py       autorrent models + MediaFile, Job, Journey, JourneyStep, Rule, PathMapping, SavingsLedger
frontend/src/pages/  Activity, Library, Search, Watchlist, Calendar, Browse, History, Insights, Settings, Updates
```

Code to reuse rather than rewrite:
- **FileSplitter media modules** (`codec_detector.py`, `encoder.py`, `scene_detector.py`, `splitter.py`, `trimmer.py`, `scene_namer.py`, `trash.py`, `scanner.py`): copied almost unchanged. They are pure functions built around `subprocess` with `progress_cb` and `cancel_event` hooks, which is exactly what the new worker needs. The only change is swapping `config.py` lookups for MediaMate's settings service.
- **FileSplitter `worker.py`**: its thread pool, pause/resume and cancel logic become `pipeline/worker.py`. It reads its queue from the `Job` table through SQLAlchemy instead of `db.py`.
- **AuTorrent `services/download_events.py` and `qbit_states.py`**: the completion detector already exists. It gains one hook: on completion, call `pipeline.on_download_complete(download)`.
- **AuTorrent `migrations.py`, `routers/backup.py`, `routers/auth.py`, `routers/onboarding.py`, `services/updater.py`**: reused as they are. Onboarding gets an import step.
- **Shared update path**: `update.sh` and `scripts/upgrade-agent.sh` already exist in both repos. Use FileSplitter's version, which adds backup, test against a copy of the database, and automatic rollback.
- **FileSplitter `static/sw.js` and `manifest.json`**: the starting point for the PWA.

## Extra features ("wow" features)
1. **Auto pipeline.** `Rule` = match conditions plus ordered actions.
   - Match conditions: codec, resolution, duration, size, keyword, source (download or scan), quality profile, watchlist item.
   - Actions: trim, split (with scene-namer naming), encode (CRF/CQ, preset, backend), rename from a template, move to a folder, refresh the media library, notify.
   - Rules are evaluated when a download completes and when a library scan finds a new file.
   - A *dry-run* preview shows which rules would fire, and the resulting size estimate, before anything is enabled.
   - **Path mapping** (qBittorrent path → container path) is a required setting, with a "Test mapping" button.
2. **Unified Live Activity.** Each item gets a `Journey` made of `JourneyStep` rows.
   - Steps: found, download, encode, split, trim, rename, library, notify. Each stores status, progress %, ETA, an ffmpeg log tail, and why it ran (for example "rule X").
   - Everything streams over one SSE channel; the 10-second polling goes away.
   - The Activity page has filter chips. Each item opens a journey drawer with Retry, Skip step and Cancel buttons.
   - A global status pill shows active and failed counts plus GB saved today.
   - Journeys are saved, so they pick up again after a restart.
   - This replaces AuTorrent's Downloads tab and FileSplitter's Active Jobs tab.
3. **Command palette + PWA.** Ctrl+K opens a palette with fuzzy actions: search torrents, add to watchlist, encode or split a file, jump to any page, pause the queue, run a scan. Also:
   - keyboard shortcuts
   - an installable PWA
   - web push (VAPID) as a notification channel alongside Apprise
4. **Savings & insights.** `SavingsLedger` records the bytes before and after every encode. The Insights page shows:
   - total GB reclaimed
   - library codec mix
   - GPU vs CPU throughput
   - a forecast of what encoding the remaining x264 files would save
   - FileSplitter's existing `saved-bytes` total, carried over

## Quality-of-life merges
- One login, one Settings page with grouped sections and a "Test" button for every integration, one updater, one backup ZIP covering everything.
- The Library page replaces FileSplitter's file table. It uses the React table pattern from autorrent's `History.tsx` and adds bulk select, preview stream, rename, delete to trash, anthology toggle, skip and queue actions.
- Encoding defaults per quality profile, so a 4K profile can automatically mean NVENC CQ22.
- Disk-space guard: AuTorrent's `disk_space.py` blocks encodes when the destination is short on space.

## Delivery phases
Each phase is a pull request on `claude/great-wright-2btctp` or a follow-up branch, with CI green before merging.

0. **Scaffold.**
   - Import the AuTorrent tree without its git history and rename the app to MediaMate.
   - Dockerfile gains ffmpeg, with an NVENC compose profile.
   - Merge the CI workflows (autorrent `build.yml` builds to GHCR; filesplitter `ci.yml` runs bash tests).
   - Port 4260 by default, so MediaMate can run alongside the old apps during migration.
1. **Media engine port.**
   - Copy the FileSplitter modules to `services/media/` and add the `MediaFile` and `Job` models plus a worker.
   - Port `tests/test_encoder.py`, `test_codec_detector.py`, `test_trash*.py` and the related suites to pytest under `backend/tests/media/`.
2. **Events + Journeys + Activity page** (SSE bus, journey tracker, Activity UI, header status pill).
3. **Pipeline:** rules engine, path mapping, completion hook, dry-run preview, rules UI in Settings.
4. **Library page + merged Settings.** FileSplitter's settings move into the AuTorrent settings model, including runtime-editable encode and scene parameters, Recheck GPU, and Trash.
5. **Importer:** an onboarding step that uploads or points at `autorrent.db` and `filesplitter.db`, maps them to the new tables, and reports what came across.
6. **Command palette + PWA + web push.**
7. **Insights page.**
8. **Parity sign-off.**
   - Work through the checklist below, then update the README with a revision history in FileSplitter's style.
   - Add pointer READMEs to the two old repos and archive them, with your confirmation before archiving.

## Parity checklist (both apps, every item must be ticked)
- **AuTorrent:**
  - Search: NYAA, TPB, EZTV, YTS, TorrentGalaxy, 1337x, Jackett/Prowlarr, with the adult filter, quality and codec filters
  - Watchlist: episodes, guide, scan, TVMaze show-ended detection, override
  - Quality profiles
  - Trakt sync
  - Calendar and Browse
  - Downloads (qBittorrent state, auto-cleanup, clear finished)
  - Duplicate detection
  - History: search, CSV export, delete
  - Plex/Jellyfin refresh and Plex digest
  - Apprise notifications
  - TMDB posters
  - Auth and token revoke
  - Onboarding wizard
  - Backup/restore
  - `/health`
  - Themes and collapsible sidebar
  - Updates page and upgrade agent
- **FileSplitter:**
  - Scan and classification (codec, duration, size, keyword, anthology)
  - x265 CPU and NVENC encoding, GPU probe with retry, Recheck GPU, fallback to CPU
  - Scene detection (select, black, freeze)
  - Split by stream copy
  - TPDB pHash scene naming
  - Intro trim
  - Rename, stream preview, delete to `.trash` with 30-day purge and Empty Trash
  - Skip and cancel; pause and resume the queue; clear jobs
  - Saved-bytes counter
  - Runtime settings
  - ffprobe timeout
  - PWA
  - Daily release check and in-app upgrade

## Verification
- `cd backend && pytest`: all of autorrent's tests plus the ported filesplitter suites pass.
- Media engine end-to-end test:
  - Generate short h264 clips with `ffmpeg -f lavfi` (testsrc plus black frames between "scenes").
  - Assert the encode produces HEVC, the split produces N files, and the trim removes about 3.5 seconds.
  - Assert the savings are recorded.
- Pipeline integration test with a mocked qBittorrent: a completed download creates a journey and the rules fire in order. SSE events are asserted with an httpx stream client.
- Importer test against fixture copies of both old database schemas.
- `cd frontend && npm run build` plus type checking.
- Playwright smoke test (Chromium is pre-installed):
  - Log in, then open each page.
  - Ctrl+K opens the palette.
  - The Activity drawer updates live.
- `docker build` succeeds, and `/health` returns OK inside the container.
- `tests/bash/*` (update and upgrade agent) pass.

---

# Phase 1: Media engine port (current step)

## Context
Phase 0 (scaffold) is merged to `main` through the PR. Phase 1 brings FileSplitter's ffmpeg engine into MediaMate as a backend that works through its API. Settings and data go into MediaMate's SQLAlchemy database. The UI comes in Phase 4 (Library page); until then the engine is usable through FastAPI's `/docs`. The work goes on a new branch from `main` (`claude/great-wright-2btctp` was merged, so I'll restart it from `main`), with a new draft PR.

## Approach: port with as few changes as possible
FileSplitter's modules read module-level `config.X` values, which are changed at runtime when settings are saved. They talk to storage only through `db.<function>()` calls. Two choices keep those modules close to unchanged:

1. **`backend/app/services/media/config.py`**: a port of FileSplitter's `config.py` with the same attribute names and the same environment-variable defaults.
   - Dropped, because MediaMate already has these: `FLASK_PORT`/`_find_free_port`, `VERSION`, `DATABASE_PATH`, `FLASK_SECRET_KEY`, `DASHBOARD_PASSWORD`, `APP_SOURCE_URL`, `APP_UPDATE_CHECK`, `APP_UPGRADE_DIR`.
   - Added: `SETTINGS_MAP` (moved from FileSplitter's `app._SETTINGS_MAP`), `apply(key, value)` (the cast logic from `api_post_settings`, including the special cases for `SPLIT_KEYWORDS` and booleans), and `load_from_db(session)` (from `_reload_settings_from_db`).
   - Settings keep **the same key names** (`x265_crf`, `scene_threshold`, and so on) in MediaMate's existing `settings` table. That makes the Phase 5 importer a straight copy.
   - `media_paths` becomes a setting you can edit, falling back to the `MEDIA_PATHS` environment variable.
2. **`backend/app/services/media/store.py`**: a replacement for `db.py` with **the same function names and return shapes** (dicts that behave like `sqlite3.Row`). It uses SQLAlchemy.
   - Functions: `upsert_file`, `set_file_status`, `set_anthology_override`, `get_file(_by_path)`, `get_all_files`, `get_stats`, `create_job`, `get_active_jobs`, `get_active_job_for_file`, `get_job`, `dequeue_next_job`, `update_job_progress`, `set_job_status`, `update_file_size`, `rename_file`, `delete_file`, `purge_missing_files`, `clear_finished_jobs`, `get_recent_jobs`, `get/set_setting`, `increment/set_saved_bytes`.
   - `recover_interrupted()` ports the crash-recovery block from `init_db`: running jobs become error, processing files go back to pending, orphaned queued files go back to pending.
   - `dequeue_next_job` uses a short transaction plus a status guard so two workers can never take the same job.

New models in `backend/app/models.py`:
- `MediaFile` (table `media_files`) and `MediaJob` (table `media_jobs`), with the columns from FileSplitter's `files` and `jobs` tables. That includes the columns added later: `width`, `height`, `anthology_override`, `saved_bytes`, `episode_count`.
- Both are created by `Base.metadata.create_all` through `run_migrations()` (in `backend/app/migrations.py`). No ALTER statements are needed for new tables.

## Files
`backend/app/services/media/`: copied from `parabyte-ca/filesplitter`. The only edits are imports (`import config` → `from . import config`, `import db` → `from . import store as db`), plus the items below.
- `codec_detector.py`, `scene_detector.py`, `splitter.py`, `trimmer.py`, `scene_namer.py`, `trash.py`, `scanner.py`: imports only.
- `encoder.py`: one real change. `NVENC_AVAILABLE = _nvenc_available()` currently runs at import time and can block for up to 120 seconds. It becomes `NVENC_AVAILABLE = False` plus `start_probe()`, which runs `_nvenc_available()` in a daemon thread and sets `PROBE_DONE` (a `threading.Event`). This keeps FastAPI startup and `/health` instant, and keeps tests from running ffmpeg on import.
- `worker.py`: copied, with `import db` changed to use the store. Before dequeuing, a worker waits on `encoder.PROBE_DONE` (up to `NVENC_PROBE_MAX_WAIT` seconds) when the backend is `nvenc`. Without this, jobs queued during startup would silently run on the CPU.
- `__init__.py`: adds `startup()` and `shutdown()` functions. `startup()` loads settings, runs `recover_interrupted()`, starts the GPU probe, and starts the worker.

`backend/app/routers/media.py`: FileSplitter's Flask API, rewritten as FastAPI under the `/api/media` prefix. Function bodies are kept.
- **Files:** `GET /files`, `GET /files/{id}/stream` (Starlette `FileResponse` supports Range requests, so seeking works), `POST /files/{id}/rename`, `/delete`, `/anthology`, `/cancel`, `/skip`, `POST /files/purge`.
- **Jobs and queue:** `GET /jobs`, `POST /jobs/{id}/cancel`, `POST /jobs/clear`, `POST /queue`, `/queue/pause`, `/queue/resume`.
- **Scan and stats:** `POST /scan` (runs in a background thread, with the `_scan_status` flag), `GET /stats`.
- **Trash:** `GET /trash`, `POST /trash/empty`.
- **Settings:** `GET` and `POST /settings`, `POST /settings/saved-bytes`, `POST /encoder/recheck-gpu`.
- **Not ported:** FileSplitter's `/stream` SSE endpoint. Phase 2's unified event stream replaces it.

Other edits:
- `backend/app/main.py`: register the router; call `media.startup()` and `media.shutdown()` in `lifespan`.
- `backend/app/services/scheduler.py`: a daily `trash.cleanup_trash(max_age_days=30)` job, replacing FileSplitter's sleep loop.
- `backend/app/middleware/auth.py`: also accept `?token=` on `GET` requests. `<video>` tags and Phase 2's `EventSource` cannot send an `Authorization` header. It uses the same `make_token` and `hmac.compare_digest` check.
- `backend/app/routers/backup.py`: no change needed, because the new tables live in the same database file.
- `backend/requirements.txt`: add `requests`, `Pillow` and `imagehash` (needed by `scene_namer`), with versions pinned as in FileSplitter.
- `docker-compose.yml` / `.env.example`: document the media environment variables (`MEDIA_PATHS`, `X265_CRF`, `SCENE_*`, `SPLIT_*`, `MAX_WORKERS`, `TPDB_API_KEY`, and so on) as comments.

## Tests (`backend/tests/media/`)
- **Ported, imports changed only:** `test_codec_detector.py`, `test_encoder.py`, `test_trash.py`.
- **Rewritten for FastAPI's TestClient:** `test_trash_routes.py`.
- **Not ported:** `test_updates_routes.py`, `test_release_cache.py`, `test_versioning.py`, `test_upgrade_state.py`, `test_agent_status.py`. These cover FileSplitter's updater, and MediaMate's own updater tests already cover the same ground.
- **New:**
  - `test_store.py`: dequeue order and no double-claiming, crash recovery, saved-bytes accounting, the anthology override.
  - `test_media_config.py`: SETTINGS_MAP casts, including the bool/int trap that FileSplitter documents in its code comments.
  - `test_media_routes.py`: every endpoint, using a temporary database and temporary media roots.
  - `test_engine_e2e.py`: skipped when ffmpeg is missing. Generates 20–40 second h264 clips with `ffmpeg -f lavfi` (testsrc with black gaps), then:
    - scans them and checks classification
    - encodes and checks the codec is hevc with savings recorded
    - splits and checks N output files
    - trims and checks the duration is about 3.5 seconds shorter
    - cancels mid-encode and checks the original file is untouched
  - `test_auth_query_token.py`.
- A conftest fixture points `media.config` at temporary media roots and uses an in-memory database.

## Verification
- `cd backend && pytest -q`: all 646 existing tests plus the new media suite pass, locally and in CI (CI already installs ffmpeg).
- Run uvicorn locally with `MEDIA_PATHS` pointing at a scratch folder of generated clips. Use `/docs` to run scan → queue encode → watch `GET /api/media/jobs` progress → stream the file with curl Range requests.
- Confirm `/health` responds immediately at startup, so the GPU probe isn't blocking anything.
- `docker build`, then `ffmpeg -version` inside the container.
- `shellcheck` and the frontend build pass as before (the frontend is untouched).

---

# Fix CI on PR #1 (in progress)

## Context
On [parabyte-ca/screenmate#1](https://github.com/parabyte-ca/screenmate/pull/1) the `backend` job fails. All 714 tests pass. The job fails at the **shellcheck** step: the runner's newer shellcheck flags SC2015 ("A && B || C is not if-then-else") on three lines inherited from AuTorrent. The older shellcheck I ran locally didn't flag them. This PR's CI step is what introduced the check, so the fix belongs here.

## Change
Rewrite each line as an explicit `if`. Behaviour stays identical: an empty key is a no-op, and a failing `declare`/`chown` is still ignored.
- `update.sh:150`: `if [[ -n "$key" ]]; then declare "$key"="$value" 2>/dev/null || true; fi`
- `update.sh:206`: `if [[ -n "$_GIT_USER" ]]; then chown "$_GIT_USER:" "$ENV_FILE" 2>/dev/null || true; fi`
- `scripts/upgrade-agent.sh:91`: same as line 150.

## Verification
- Install the latest `shellcheck-py`, reproduce SC2015 on the current head, apply the fix, and show shellcheck exiting with 0.
- `pytest -q tests/test_update_script.py` (it exercises update.sh), then the full suite.
- Push to `claude/great-wright-2btctp`; CI `backend` and `frontend` should both be green.

PR #2 is merged and `main` is green.

---

# Phase 2: Live events, Journeys and the Activity page (current step)

## Context
Today the downloads page polls every 10 s. qBittorrent progress is only refreshed when someone loads that page, and completed downloads are only noticed by the hourly watchlist scan. The media engine (Phase 1) has no UI at all. Phase 2 adds:
- one live event stream
- a saved **Journey** for each item
- an **Activity** page that replaces Downloads, plus a global status pill

The branch is `claude/great-wright-2btctp`, freshly cut from `main`, with a new draft PR. The pipeline that links a download to its encode is Phase 3; Phase 2 adds the `journey_id` column that link will use.

## Design: derive steps instead of copying them
A journey's steps are **read from the rows that already exist**, not copied into new tables. That way they can't drift out of sync, and they survive restarts for free.
- `found` and `download` steps come from the `Download` row, plus a live progress cache from qBittorrent.
- `encode`, `split` and `trim` steps come from `MediaJob` rows that carry the journey's `journey_id`, including the job's `log_tail`, `progress_pct` and `saved_bytes`.
- Phase 3 adds `library`, `notify` and `rename` steps, and the reason each step ran.

New table `journeys` (in `models.py`):
- `id`, `title`, `source` (`download` or `library`)
- `download_id` (unique, nullable), `media_file_id` (nullable)
- `status` (`active`, `done`, `failed` or `cancelled`), stored so filters and counts stay cheap
- `created_at`, `updated_at`

Migration: `ALTER TABLE media_jobs ADD COLUMN journey_id INTEGER`. The `media_jobs` table already exists in Phase 1 databases, so this goes in the `MIGRATIONS` list in `migrations.py`.

## Backend
1. **`services/events.py`**: a thread-safe pub/sub bus.
   - `publish(type, data)` is safe to call from worker, scheduler and request threads.
   - Each SSE subscriber gets an `asyncio.Queue`, fed through `loop.call_soon_threadsafe`.
   - A ring buffer of the last 500 events with increasing ids lets a client that reconnects with `Last-Event-ID` replay what it missed. If the gap is too large, it gets a `resync` event instead.
   - `progress` events are throttled to at most one per second per step.
2. **`routers/events.py`**: `GET /api/events`, the SSE stream.
   - It sends a `: keepalive` comment every 15 s (FileSplitter did the same because of Cloudflare's idle timeout).
   - Auth uses `?token=` from Phase 1. Exempt it from GZip.
3. **`services/journeys.py`**:
   - `for_download(db, dl)`: get or create the journey, titled with the download title.
   - `for_media_file(db, file_id)`: get or create one journey per file (`source=library`).
   - `refresh(db, journey_id)`: recompute the status and publish `journey.updated` with the full snapshot.
   - `snapshot(j)`: build the steps as described above.
   - `stats()`: active count, failed in the last 24 h, bytes saved today (the sum of `media_jobs.saved_bytes` with `finished_at` today), and whether the queue is paused.
4. **Hooks, one per source, so no call sites need changing:**
   - **Downloads:** a SQLAlchemy `after_flush` listener on the session collects `Download` rows that were inserted or had their `status` changed (via attribute history). After commit, it creates or refreshes their journeys in a fresh session. This covers every place downloads are written: `routers/downloads.py:300`, `services/grab.py:75`, `services/scheduler.py:194`, `services/download_events.py:188/237`, and both status-sync paths.
   - **Media jobs:** `services/media/store.py` is already the single choke point.
     - `create_job` takes an optional `journey_id` and defaults it to `for_media_file`.
     - `set_job_status` calls `journeys.refresh`.
     - `update_job_progress` publishes a throttled `progress` event and does not write to the journey.
5. **`services/download_poller.py`**: a live download feed.
   - A daemon thread runs every 3 s, but only while at least one SSE client is connected and there are active downloads.
   - It makes one `get_all_torrent_statuses()` call (`services/qbittorrent.py:84`) and publishes `progress` events with percentage, speed and ETA.
   - When a torrent enters `QBITTORRENT_COMPLETE_STATES` (`services/qbit_states.py`) it calls the existing `scheduler._sync_download_statuses()`. Completion then shows up within about 3 s instead of up to an hour later.
6. **`routers/activity.py`**:
   - `GET /api/activity?filter=all|active|failed|done_today&limit=&before=` lists journey snapshots, newest-updated first.
   - `GET /api/activity/{id}` returns the full snapshot with log tails.
   - `GET /api/activity/stats`.
   - `POST /api/activity/{id}/retry` and `POST /api/activity/{id}/cancel` act on the journey's current step.
     - **Download step:** Retry reuses `download_events.retry_download`; Cancel reuses the existing download delete logic.
     - **Media steps:** Retry re-queues the same job type (`store.create_job` with the same `journey_id`); Cancel reuses `routers/media._cancel_job_by_id`.
   - A Skip step action comes in Phase 3 with the pipeline.
   - `POST /api/activity/clear-finished`, which subsumes `downloads.clearFinished`.
7. **Backfill:** on startup, create journeys for any `Download` or `MediaJob` that doesn't have one yet. It's idempotent, so existing installs get a populated Activity page.

## Frontend
- `src/events.tsx`: `EventsProvider` plus a `useEvent(type, handler)` hook.
  - One shared `EventSource('/api/events?token=…')`, with a reconnect that backs off.
  - Exposes the connection state, which the pill shows as a "live" dot.
- `src/pages/Activity.tsx`, replacing `Downloads.tsx`:
  - filter chips: All, Active, Failed, Done today
  - journey cards with a step strip (icons for found → download → encode/split/trim), the current step's progress bar, speed and ETA
  - the poster from the watchlist item when there is one
- **Kept from Downloads (parity):** the stalled banner with its Re-search link, removing an entry, and Clear finished.
- `src/components/JourneyDrawer.tsx`: a slide-over panel opened from a card.
  - a timeline of steps with start and finish times and duration
  - a collapsible ffmpeg or log tail
  - Retry and Cancel buttons
  - the result of each encode ("Saved 1.3 GB (−62 %)")
- `src/components/StatusPill.tsx`: sits in the `Layout.tsx` sidebar header and the mobile header.
  - Shows "3 active · 1 failed · 12.4 GB saved today".
  - Clicking it opens /activity.
  - Updates live from `stats` events.
- Routing: `/activity` is added. `/downloads` becomes a `<Navigate to="/activity">`, so old bookmarks keep working. The nav entry "Downloads" becomes "Activity".
- The `api.activity.*` types and calls go in `src/api/client.ts`, following the existing `req<T>` pattern.

## Tests
- `tests/test_events.py`:
  - publishing from several threads reaches every subscriber
  - `Last-Event-ID` replay, and `resync` when the gap is too large
  - progress throttling
  - an SSE stream test using `httpx`/TestClient that checks keepalives and that `?token=` auth applies
- `tests/test_journeys.py`:
  - creating a download through each write path (`grab_episode`, `add_download`, `retry_download`) creates exactly one journey
  - status changes refresh it
  - media jobs attach to their file's journey
  - the snapshot has the right step order and statuses
  - stats counts, including saved today
  - backfill is idempotent
- `tests/test_activity_routes.py`: filters, pagination, retry and cancel for both download and media steps, clear-finished.
- `tests/test_download_poller.py`: with qBittorrent mocked, the poller publishes progress and triggers a sync when a torrent completes; with no subscribers it stays idle.
- Frontend: `npm run build` (tsc). Playwright smoke test against uvicorn with a generated clip:
  - open /activity
  - queue an encode through the API and watch the card's progress move
  - the drawer shows the log
  - the pill counts update
  - `/downloads` redirects

## Verification
- `pytest -q` passes, all existing tests plus the new ones.
- Live: run uvicorn with media paths pointing at generated clips. Watch the stream with `curl -N "localhost:8765/api/events"` while queueing an encode, and check that `progress` and `journey.updated` events arrive. Then take Playwright screenshots of the Activity page and drawer in light and dark themes.
- CI is green on the new PR. `docker build` still can't run in this environment; CI builds the image on `main`.

# Phase 3: Auto pipeline (delivered)
- Rules: conditions plus ordered steps (trim → encode → split), with templates, reordering, and a preview in the editor.
- Plans run one step per file at a time and advance as each job finishes, then refresh the library and notify.
- Downloads: the torrent is removed from qBittorrent first (files kept), or the plan waits while it's still seeding.
- Path mappings with a Test button; a library dry run with savings estimates and Apply; a disk-space guard; tick() every 2 min resumes plans.
- Deferred to a later phase: the rename-from-template and move-to-folder steps (both conflict with seeding and library paths; they need their own design).

# Phase 4: Library + Media engine settings (delivered)
- Library page, matching FileSplitter's Files tab: stats tiles, 7 filters plus search, pagination, per-file preview/encode/split/trim/rename/skip/cancel/anthology/delete, a bulk bar, encode-all and split-all.
- Settings → Media engine: media folders, GPU status and Recheck, encoder/CRF/preset/resolution, simultaneous jobs (applies live), ffprobe timeout, anthology and split settings, scene detection, TPDB key, saved-space counter, trash.
- Worker fix: jobs are claimed only when a slot is free (FileSplitter marked every queued job running at once).

# Phase 5: Importer + setup.sh (delivered)
- Upload autorrent.db and/or filesplitter.db (detected by their tables), preview the counts, then import in one transaction after a backup. IDs are remapped, duplicates skipped, existing settings kept unless you choose to overwrite, and secrets are never imported. FileSplitter paths can be rewritten on the way in.
- Available as a step in the setup wizard and in Settings → Data management.
- setup.sh: an interactive or unattended installer (port, time zone, media mounts, GPU, build or pull, upgrade agent) that writes .env and docker-compose.override.yml; update.sh now honours the override.

# Phase 6: Command palette, PWA, push (delivered)
- Ctrl/⌘+K or "/" opens a palette: pages, actions (scan, pause/resume, clear, theme), torrent search, and live shows/files/activity. "g"+letter jumps to a page.
- PWA: real manifest with shortcuts, an Install button, and a service worker that no longer serves the SPA shell for /api or caches API responses.
- Web Push: VAPID keys created automatically, devices managed in Settings → Notifications, and every existing alert also goes to subscribed phones and desktops. Needs HTTPS.

# Phase 7: Insights (delivered)
- Insights page (`/insights`, `g i`): all-time space saved, then figures for the last 7, 30 or 90 days: saved per day, downloads per day, library by codec and by resolution, encoding speed per encoder (× real time), and the biggest wins.
- "What's left to gain": how much the files that aren't HEVC yet would save, at your own past ratio, and how long that would take on the current encoder. It links to the Library and the Auto pipeline.
- Everything is worked out from existing tables (no separate ledger to drift). Encodes now record which encoder ran them and how many seconds of video they covered.
- Click any poster for a larger preview.

# Phase 8: Parity sign-off (in progress)
- [docs/PARITY.md](PARITY.md): every AuTorrent and FileSplitter feature, where it lives now, and the test or smoke check that proves it.
- New tests for what had none: NYAA/EZTV/YTS/TorrentGalaxy/1337x parsing, sign out everywhere, removing finished torrents, each scene-detection method on a real clip, TPDB scene naming, NVENC → CPU fallback, and the Plex digest.
- `frontend/tests/smoke.cjs`: 28 browser checks (every page in both themes, theme and sidebar, palette, backup export, scan → encode, phone navigation).
- Fixed: scene detection "select" (and so "auto") never found a cut.
- Next: 1.0.0, then archive the old repositories once you confirm.

## Follow-ups to flag
- The `anthropic-skills:autorrent` skill will be out of date. Once Phase 3 lands, replace it with a `screenmate` skill (use `skill-creator`).
- The `homelab` and `plex` skills may refer to the old ports and containers. Review them after migration.
