# ScreenMate

**[Latest release](https://github.com/parabyte-ca/screenmate/releases/latest)** · the version you run is shown at the bottom of the sidebar.

A self-hosted media pipeline. It finds shows and movies, downloads them through qBittorrent, then encodes them to x265, splits anthologies, trims intros, renames, and refreshes Plex/Jellyfin, all without you touching a file.

ScreenMate merges [AuTorrent](https://github.com/parabyte-ca/autorrent) (search, watchlist, downloads) and [FileSplitter](https://github.com/parabyte-ca/filesplitter) (scan, encode, split) into one app with one UI, one login, one database and one container.

> **Status:** parity sign-off. Every AuTorrent and FileSplitter feature is in ScreenMate. [docs/PARITY.md](docs/PARITY.md) lists each one, where it is now, and the test that proves it. Next comes 1.0.0. See [docs/ROADMAP.md](docs/ROADMAP.md).

---

## Highlights

- **Auto pipeline:** rules like "if h264, encode to x265; if anthology, split" run automatically when a download completes or a scan finds a file. A dry-run preview shows what would happen first.
- **Unified Live Activity:** every item has one live timeline from found → downloaded → encoded → split → in the library, with retry and cancel at any step.
- **Command palette + PWA:** Ctrl+K for everything, an installable app, and push notifications.
- **Savings & insights:** GB reclaimed, library codec mix, GPU vs CPU throughput, and a forecast of what encoding the rest would save.
- **One-click import** of existing AuTorrent and FileSplitter databases.
- **Rename & organise:** finished files move into your TV and Movies folders with Plex-style names (`Show/Season 01/Show - S01E02.mkv`, `Title (2020)/Title (2020).mkv`), subtitles included. This runs as an Auto pipeline step or from the Library, one file or many at a time.
- **Mature folders:** optional Mature TV and Mature Movies folders. Each title goes by its age rating from TMDB: up to PG-13 / TV-14 in the regular folders, and R, TV-MA and anything unrated in the Mature ones. A show's *Who it's for* setting on the Watchlist overrides this, and the Library doctor flags titles in the wrong folder. In Plex, share only the regular libraries with kids' accounts.
- **Browse movies:** Browse has a TV shows / Movies switch. Movies come from TMDB (Popular, In theatres and Coming soon, or a search) and show the age rating when it's known. Ones already in your library are marked, and **Find downloads** opens Search for any other.
- **Track clean-up:** encodes keep English plus the original-language audio, and English and forced subtitles, and drop the rest. This often saves another 10–20%. The languages are set in Settings → Media engine.
- **Library doctor:** a weekly check (Library → Library doctor, or Check now) for sample clips, leftover files, empty folders, unplayable files, missing files, duplicates (the best copy is kept) and files not named for Plex. The first three are fixed automatically. The rest get Fix buttons. Removed files go to the 30-day trash.
- **Music:** follow an artist on the Watchlist's **Music** tab (found through MusicBrainz, free, no key). Each new album downloads once it's out, in FLAC; MP3 320 is taken if no FLAC turns up within two days, and low-bitrate copies never. When you follow, choose new releases only, the latest album too, or everything. Each album can be wanted, skipped or downloaded now. Albums use their own qBittorrent category (`music`) and download folder (Settings → qBittorrent).
- **Stars and collections, synced with Plex:** rate files (1–5 stars) and add them to collections in the Library, one at a time or in bulk, then filter by either. Turn on *Sync star ratings & collections with Plex* (Settings → Plex) and they show up in Plex. Ratings and collections set in Plex appear in the Library too (every 30 minutes, or Sync now). If both sides change, Plex wins. Files are matched by name, so Plex's paths don't need to match ScreenMate's. An episode's collections are its show's, because Plex only collects movies and shows. Protected folders are never sent.
- **Adult content stays out of Plex:** mark a folder **Protected** (Settings → Folders; Bellesa is protected automatically) and nothing ever renames or moves its files. Rename & organise also refuses any file that looks like adult content (studio and site names such as Vixen, Tushy, OnlyFans, FrolicMe, wherever it is), so neither the Library, the Library doctor nor the auto pipeline can put one in a TV or Movies folder. The Library doctor's **Adult content in a Plex folder** check finds files moved there earlier and moves them back; Library → **Move to folder** handles anything else.
- **Target formats per folder:** each folder in Settings → Folders has a target, all H.265: TV shows 720p–1080p, Movies 1080p–4K, Custom (e.g. Bellesa: 720p, shrinking bigger files when they're encoded), or Auto (by file name). Files already H.265 show as **on target** instead of pending, so "Encode all pending" leaves them alone. H.265 files bigger than the target are never shrunk without asking: the Library doctor lists them ("Larger than target", with a Shrink button), along with files "Lower than preferred". New TV shows download 1080p (720p fallback) and new movies 4K (1080p fallback) by default (Settings → Quality Profiles).
- **Movies on the Watchlist:** TV shows and movies have their own tabs and badges. Add a movie from Browse (Coming soon, In theatres, Popular) or with **Add → Movie**: it waits for the digital release date from TMDB (or the disc date, or about 90 days after cinemas), downloads the best release once, and leaves the Watchlist 30 days after the download finishes (Settings → Auto-scanner; pin a movie to keep it). The file stays in the Library.
- **Better show matching:** an exact name wins over TVMaze's fuzzy guess (so "Gone" no longer becomes "I'll Be Gone in the Dark"). When you add a show, pick it from the suggestions so nothing is guessed. If a show is still wrong, click **Wrong show?** on its Watchlist card and choose the right one; the poster, episode guide and status follow it.
- **Guided tour** for new users: a one-minute walk through Search, Watchlist (shows and movies), Browse, Calendar, Activity, Library and the rest. It starts once on each new device (and once more on every device when the tour changes), and you can replay it from Settings → Appearance or by searching "tour" with Ctrl+K.

---

## Quick Start

**Requirements:** Docker with the Compose plugin, and git.

```bash
git clone https://github.com/parabyte-ca/screenmate.git
cd screenmate
./setup.sh
```

`setup.sh` asks for the port (default 4260), your time zone and your media folders, and spots an NVIDIA GPU if you have one. It then:
- pulls the image and starts the container
- waits until the app is healthy
- offers to install the in-app upgrade helper
- prints the address to open

Your answers are saved to `.env` and `docker-compose.override.yml`. Both files stay out of git and are kept by `./update.sh`. Run `./setup.sh` again at any time to change them.

Unattended install:

```bash
./setup.sh -y --media /mnt/nas/tv --media /mnt/nas/movies --port 4260
```

Other flags: `--gpu` / `--no-gpu`, `--build` (build from this checkout instead of pulling), `--tz America/Toronto`, `--agent` / `--no-agent`. See `./setup.sh --help`.

Media folders are mounted at the same path inside the container. When qBittorrent runs on the same host, its download paths therefore match without any path mapping.

**Folders** (in Settings) is one list for both halves of the app. Each folder can take downloads, be scanned and encoded, or both. The folders you give `setup.sh --media` appear there automatically. A folder qBittorrent sees under a different path (another container, another machine) is linked by a path mapping or its "Path here" field. When you upgrade from an earlier version, the old download folders and media folders are merged into this list automatically, and nothing that was scanned before stops being scanned.

Each folder has a **Test** button (and **Test all**). It checks that ScreenMate can see, read and write the folder, how much space is free, and, for download folders on qBittorrent 5 or later, that qBittorrent can see it too. When something fails, it says how to fix it, such as the volume line to add to `docker-compose.override.yml`. When qBittorrent is connected, the folders it saves into (its default folder, each category's folder and the folders its torrents are in) that aren't in the list yet appear under **Found in qBittorrent**. Each one shows where ScreenMate sees it, and **Add** adds it in one click.

**Coming from AuTorrent or FileSplitter?** The setup wizard (or **Settings → Data management**) imports your old `autorrent.db` and `filesplitter.db` with one upload each.

---

## Updating ScreenMate

ScreenMate is automatically built and published to GitHub Container Registry on every push to `main`.

To update your running instance:

```bash
cd /path/to/screenmate
./update.sh
```

Or manually:

```bash
docker pull ghcr.io/parabyte-ca/screenmate:latest
docker compose up -d
```

### Versions and rolling back

`update.sh` (and the in-app upgrade) installs an exact release and records it as `SCREENMATE_VERSION` in `.env`; `docker-compose.yml` uses it as the image tag (`ghcr.io/parabyte-ca/screenmate:${SCREENMATE_VERSION:-latest}`). That is what makes going back possible:

- **Automatic** — if the new version does not become healthy, `update.sh` puts the previous release back.
- **From the app** — after an upgrade, the **Updates** page shows **Roll back to vX.Y.Z**.
- **By hand** — `./update.sh --version 2.6.1` installs any published release, older ones included.

Rolling back keeps your data; if you have used features added since, take a backup first (Settings → Data management). Delete the `SCREENMATE_VERSION` line from `.env` to follow `latest` again.

You can also update from inside the app: the **Updates** page shows when a new release is available and can install it for you (see `scripts/upgrade-agent.sh`).

Your data is stored in `./data/` and is not affected by updates.

> **First-time setup:** After the first successful GitHub Actions build, the package visibility must be set to **Public** so the image can be pulled without authentication. Go to `https://github.com/parabyte-ca?tab=packages`, find the `screenmate` package, and set its visibility to Public.

---

## Moving from AuTorrent and FileSplitter

ScreenMate runs on its own port (4260), so you can keep the old apps running until you're happy.

1. Install ScreenMate with `./setup.sh`. Give it the same media folders FileSplitter used (`--media /path`, repeat for each).
2. In the setup wizard, or later in **Settings → Data management → Import**, upload `autorrent.db` and `filesplitter.db` from each app's `data/` folder. Nothing changes until you press **Import**. Before that, ScreenMate shows you:
   - what each file contains,
   - the settings that differ from this install, one choice per setting (versions and update details are never imported),
   - where your old files and qBittorrent folders are on this server, with suggested path mappings.
3. Your current database is backed up first, and importing twice is safe.
4. Check **Activity**, **Library** and **Watchlist**, then stop the old containers: `docker compose down` in each app's folder. Point any bookmarks at port 4260.

---

## Development

**Requirements:** Python 3.11+, Node.js 20+

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
DATABASE_URL=sqlite:///./data/screenmate.db uvicorn app.main:app --reload --port 8000
```

```bash
# Frontend (separate terminal)
cd frontend
npm install
npm run dev   # runs on http://localhost:5173, proxies /api and /health to :8000
```

The Vite dev server proxies `/api` and `/health` to the FastAPI backend, so hot-reload works for both sides independently.

```bash
# Run backend tests
cd backend
python -m pytest tests/ -v
```

---

## Contributing

Bug reports and pull requests are welcome. Please open an issue first for any significant changes so we can discuss the approach before you invest time in it.

### Releases are automatic

Merging to `main` publishes a release when the merged change warrants one — you don't create tags by hand. The pull request **title** decides (use the [conventional commit](https://www.conventionalcommits.org) style):

| PR title | Release |
|---|---|
| `feat: …` | minor — `2.6.1` → `2.7.0` |
| `fix: …`, `perf: …` | patch — `2.6.0` → `2.6.1` |
| `feat!: …`, `fix!: …`, or a `BREAKING CHANGE:` line in the body | major — `2.7.0` → `3.0.0` |
| `docs:`, `ci:`, `test:`, `chore:`, `refactor:`, `style:`, `build:` | none (an image is still built) |

The very first release is the version written in the source (`backend/app/routers/health.py`), published by the first `feat:`/`fix:` merge. A check fails any PR whose title doesn't start with one of these types. Add `[skip release]` to a PR title to merge without releasing. When several PRs merge before a build runs, the biggest change wins. A release builds the image (`vX.Y.Z`, `X.Y` and `latest`), then creates the git tag and GitHub release; the in-app **Updates** page offers it. **Release button:** to release right now whatever the titles say — say, a `docs:` merge you do want shipped, or a bigger bump — open the repository's **Actions** tab → *Build and push Docker image* → **Run workflow**, keep branch `main`, and pick `patch`, `minor` or `major`. (`none` just builds the image.) It needs something new since the last release, and ignores `[skip release]`. Pushing a `vX.Y.Z` tag yourself still works and builds exactly that version. The logic lives in `scripts/next_version.py`.

---

## Revision history

Releases after 0.8.0 are listed on the [Releases page](https://github.com/parabyte-ca/screenmate/releases).

| Version | What came in |
|---|---|
| 0.8.1 | `update.sh` repairs file ownership and folder permissions before it pulls. |
| 0.8.0 | First release. Phases 0–7 below, plus: secrets kept off the browser (security audit), new app icon, poster preview, settings comparison and path suggestions on import, Updates under **More** on phones. |
| Phase 7 | Insights: space saved, codec and resolution mix, encoder speed, what's left to gain. |
| Phase 6 | Command palette (Ctrl/⌘+K), installable app, push notifications. |
| Phase 5 | One-click import from AuTorrent and FileSplitter; `setup.sh` installer. |
| Phase 4 | Library page and Media engine settings. |
| Phase 3 | Auto pipeline: rules, path mappings, dry run. |
| Phase 2 | Live Activity page with journeys and an event stream. |
| Phase 1 | FileSplitter's media engine ported: scan, x265/NVENC encode, split, trim, scene naming, trash. |
| Phase 0 | Scaffold from AuTorrent: search, watchlist, downloads, history, Plex/Jellyfin, notifications, updates. |

---

## License

MIT
