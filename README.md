# MediaMate

**[Latest release](https://github.com/parabyte-ca/mediamate/releases/latest)** · the version you run is shown at the bottom of the sidebar.

A self-hosted media pipeline. It finds shows and movies, downloads them through qBittorrent, then encodes them to x265, splits anthologies, trims intros, renames, and refreshes Plex/Jellyfin, all without you touching a file.

MediaMate merges [AuTorrent](https://github.com/parabyte-ca/autorrent) (search, watchlist, downloads) and [FileSplitter](https://github.com/parabyte-ca/filesplitter) (scan, encode, split) into one app with one UI, one login, one database and one container.

> **Status:** parity sign-off. Every AuTorrent and FileSplitter feature is in MediaMate. [docs/PARITY.md](docs/PARITY.md) lists each one, where it is now, and the test that proves it. Next comes 1.0.0. See [docs/ROADMAP.md](docs/ROADMAP.md).

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
- **Movie genre folders and nightly organising:** in Settings → Auto pipeline → Rename & organise, turn on **Movie genre folders** (Movies / Genre / Title (Year), using the genre folders you already have and TMDB's genres; Holiday, _Extras and _Unsorted are left alone) and **Organise new files every night** (2:07 local). Loose files in the TV and movie libraries are named and put in place; anything already organised stays put, unreadable names go to `_Unsorted`, and you're notified only when something needs a look. **Show what would move** previews a run.
- **Browse → Music:** new releases and popular albums (ListenBrainz), ranked **for you** from your music library and followed artists (their genres, looked up once on MusicBrainz). **Add** follows the artist for future releases and downloads that album.
- **No duplicate album downloads:** before an album downloads, MediaMate checks your music library. If you already have it, it's marked Downloaded; if your copy is lossy, low bitrate or missing tracks, a FLAC copy is fetched and replaces it (the old files go to the 30-day trash, notes and cover are kept).
- **Music doctor:** the same weekly check for the music library (Library → Music → Music doctor). It finds leftover download files (`.cue`, `.log`, `.nfo` and `.txt` notes are kept), empty folders, unreadable tracks, duplicate albums (the lossless or higher-bitrate copy is kept), albums not organised as Artist / Album (Year) / 01 - Title, missing tags, missing artwork and covers not inside the files. Tags and artwork are fixed in the background from MusicBrainz. Albums with missing tracks or a low bitrate can be marked Wanted on the artist's Watchlist card, so a better copy downloads.
- **People:** one shared library, a watchlist view per person (Settings → People). Each phone or browser remembers who's using it, chosen the first time it opens MediaMate and switchable from the menu. Watchlist, Calendar and Activity show **Mine** or **Everyone**, and each person can have a picture. A show is downloaded once however many people follow it; removing a shared show only takes it off your list. Phone alerts about a show go to the people following it, and system alerts (updates, disk space, organise reports) to the owner.
- **Music:** follow an artist on the Watchlist's **Music** tab (found through MusicBrainz, free, no key). Each new album downloads once it's out, in FLAC; MP3 320 is taken if no FLAC turns up within two days, and low-bitrate copies never. When you follow, choose new releases only, the latest album too, or everything. Each album can be wanted, skipped or downloaded now. Albums use their own qBittorrent category (`music`) and download folder (a folder with **Album downloads go here** in Settings → Folders).
- **Music library:** Library → **Music** lists albums from the music library (the folder whose **Library for** is Music library, in Settings → Folders) and folders with the **Music** target. Finished album downloads get missing tags and cover art from MusicBrainz, then are linked into the music library as `Artist/Album (Year)/01 - Title.flac`, so the torrent keeps seeding. Play tracks, rate albums and add them to collections; these sync with Plex like videos. **Fix tags** and **Organise for Plex** work on any album. Albums without a cover get one: from the artwork inside the files first, otherwise from MusicBrainz. This happens on import, in the daily scan, or with **Get cover**. Existing pictures are never replaced. **Edit tags** lets you change album and track tags by hand: fill them from file names (including etree-style `d1t03` bootleg names), number tracks in order, and set live-recording details (show date, venue and city become the album title and a comment). **Change cover** uploads a picture or picks one from MusicBrainz, and can also put it inside every track. Switch between **tiles** and a **list**, and tick several albums to **Find artwork**, **Fix tags**, **Organise for Plex**, **Put covers in files**, rate them or add them to a collection in one go. Long jobs run in the background with a progress bar; *select N without artwork* picks every album still missing a cover. Albums with missing tags, missing tracks, low-bitrate copies (lossy under 220 kbps) or duplicates are flagged.
- **Stars and collections, synced with Plex:** rate files (1–5 stars) and add them to collections in the Library, one at a time or in bulk, then filter by either. Turn on *Sync star ratings & collections with Plex* (Settings → Plex) and they show up in Plex. Ratings and collections set in Plex appear in the Library too (every 30 minutes, or Sync now). If both sides change, Plex wins. Files are matched by name, so Plex's paths don't need to match MediaMate's. An episode's collections are its show's, because Plex only collects movies and shows. Protected folders are never sent.
- **Adult content stays out of Plex:** mark a folder **Protected** (Settings → Folders; Bellesa is protected automatically) and nothing ever renames or moves its files. Rename & organise also refuses any file that looks like adult content (studio and site names such as Vixen, Tushy, OnlyFans, FrolicMe, wherever it is), so neither the Library, the Library doctor nor the auto pipeline can put one in a TV or Movies folder. The Library doctor's **Adult content in a Plex folder** check finds files moved there earlier and moves them back; Library → **Move to folder** handles anything else.
- **Target formats per folder:** each folder in Settings → Folders has a target, all H.265: TV shows 720p–1080p, Movies 1080p–4K, Custom (e.g. Bellesa: 720p, shrinking bigger files when they're encoded), or Auto (by file name). Files already H.265 show as **on target** instead of pending, so "Encode all pending" leaves them alone. H.265 files bigger than the target are never shrunk without asking: the Library doctor lists them ("Larger than target", with a Shrink button), along with files "Lower than preferred". New TV shows download 1080p (720p fallback) and new movies 4K (1080p fallback) by default (Settings → Quality Profiles).
- **Movies on the Watchlist:** TV shows and movies have their own tabs and badges. Add a movie from Browse (Coming soon, In theatres, Popular) or with **Add → Movie**: it waits for the digital release date from TMDB (or the disc date, or about 90 days after cinemas), downloads the best release once, and leaves the Watchlist 30 days after the download finishes (Settings → Auto-scanner; pin a movie to keep it). The file stays in the Library.
- **Better show matching:** an exact name wins over TVMaze's fuzzy guess (so "Gone" no longer becomes "I'll Be Gone in the Dark"). When you add a show, pick it from the suggestions so nothing is guessed. If a show is still wrong, click **Wrong show?** on its Watchlist card and choose the right one; the poster, episode guide and status follow it.
- **Guided tour** for new users: a short walk through who you are on this device, Search, the Watchlist (Mine or Everyone, who follows what), Browse, Calendar, Activity, Library and alerts. It starts once on each new device, after it has said who is using it (and once more on every device when the tour changes), and you can replay it from Settings → Appearance or by searching "tour" with Ctrl+K. With only one person set up it points to Settings → People instead.

---

## Quick Start

**Requirements:** Docker with the Compose plugin, and git.

```bash
git clone https://github.com/parabyte-ca/mediamate.git
cd mediamate
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

Each folder has a **Test** button (and **Test all**). It checks that MediaMate can see, read and write the folder, how much space is free, and, for download folders on qBittorrent 5 or later, that qBittorrent can see it too. When something fails, it says how to fix it, such as the volume line to add to `docker-compose.override.yml`. When qBittorrent is connected, the folders it saves into (its default folder, each category's folder and the folders its torrents are in) that aren't in the list yet appear under **Found in qBittorrent**. Each one shows where MediaMate sees it, and **Add** adds it in one click.

**Coming from AuTorrent or FileSplitter?** The setup wizard (or **Settings → Data management**) imports your old `autorrent.db` and `filesplitter.db` with one upload each.

---

## Updating MediaMate

MediaMate is automatically built and published to GitHub Container Registry on every push to `main`.

To update your running instance:

```bash
cd /path/to/mediamate   # an install from before the rename still lives in .../screenmate
./update.sh
```

Or manually:

```bash
docker compose pull
docker compose up -d
```

### Versions and rolling back

`update.sh` (and the in-app upgrade) installs an exact release and records it as `SCREENMATE_VERSION` in `.env`; `docker-compose.yml` uses it as the image tag (`ghcr.io/parabyte-ca/mediamate:${SCREENMATE_VERSION:-latest}`). That is what makes going back possible:

- **Automatic** — if the new version does not become healthy, `update.sh` puts the previous release back.
- **From the app** — after an upgrade, the **Updates** page shows **Roll back to vX.Y.Z**.
- **By hand** — `./update.sh --version 2.6.1` installs any published release, older ones included.

Rolling back keeps your data; if you have used features added since, take a backup first (Settings → Data management). Delete the `SCREENMATE_VERSION` line from `.env` to follow `latest` again.

You can also update from inside the app: the **Updates** page shows when a new release is available and can install it for you (see `scripts/upgrade-agent.sh`).

Your data is stored in `./data/` and is not affected by updates.

> **First-time setup:** After the first successful GitHub Actions build, the package visibility must be set to **Public** so the image can be pulled without authentication. Go to `https://github.com/parabyte-ca?tab=packages`, open each package (`mediamate`, and `screenmate` while both are published), and set its visibility to Public.

### Folders: one list

Every folder is set in **Settings → Folders**. Besides *Downloads go here* and *Scan & encode*, a folder can be:

- **Library for** TV, Movies, Mature TV, Mature Movies or Music. Rename & organise puts finished files there, named for Plex. Each library is one folder. Protected folders can't be a library.
- **Album downloads go here**: where music downloads land.

The old typed paths (Rename & organise, Settings → Music, the qBittorrent music folder) moved onto folders automatically on the first start after upgrading. Folders whose paths differ only in capital letters, and scanned folders inside other scanned folders, are flagged in the list.

### ScreenMate is now MediaMate

The app was renamed when it gained music. An install from before the rename moves over by itself on the next upgrade (Updates page or `./update.sh`):

- The container is now called `mediamate` (`docker logs mediamate`). Your data in `./data` stays where it is.
- Existing installs keep the qBittorrent category `screenmate`; new installs use `mediamate`.
- Some internal names stay as they were so upgrades and rollbacks keep working: the `SCREENMATE_VERSION` and `SCREENMATE_PORT` lines in `.env`, the compose service name, the `screenmate.db` file and the folder you installed into.
- Installs now pull `ghcr.io/parabyte-ca/mediamate`. The next upgrade fetches the new `docker-compose.yml` and switches over by itself. Images are still published as `ghcr.io/parabyte-ca/screenmate` too, so an older install keeps getting updates until it moves, and rolling back to a release from before the switch still works.
- The repository is now `parabyte-ca/mediamate`. GitHub redirects the old address, so `git pull` keeps working.

---

## Moving from AuTorrent and FileSplitter

MediaMate runs on its own port (4260), so you can keep the old apps running until you're happy.

1. Install MediaMate with `./setup.sh`. Give it the same media folders FileSplitter used (`--media /path`, repeat for each).
2. In the setup wizard, or later in **Settings → Data management → Import**, upload `autorrent.db` and `filesplitter.db` from each app's `data/` folder. Nothing changes until you press **Import**. Before that, MediaMate shows you:
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

Releases after 0.8.0 are listed on the [Releases page](https://github.com/parabyte-ca/mediamate/releases).

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
