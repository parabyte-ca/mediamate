# Security audit: secrets and the client

**Date:** 2026-10-31
**Scope:**
- the backend code and frontend source
- the production bundle (`frontend/dist`)
- source maps
- the Dockerfile and build context
- the CI workflows
- `.env` handling
- the full git history

**Question:** can a credential, API key, token, private key or connection string reach the browser, the bundle, the image or the logs?

## Result
The frontend bundle, the image layout and the git history were already clean. The API was not: one endpoint returned every stored secret in clear text. That is fixed, and CI now checks for regressions on every push.

## Findings

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| 1 | High | `GET /api/settings` and the `PUT` response returned the stored secrets in clear text: qBittorrent password, Plex token, Jellyfin, Jackett/Prowlarr and TMDB keys, Trakt client ID, Apprise URL (holds bot tokens), SMTP password, the UI password and the **VAPID private key**. Requests from the local network need no login (by design), so anyone on the LAN could read them. | Fixed |
| 2 | Medium | Error text from the Plex, Jellyfin and Jackett test buttons and from Plex digest calls can contain the request URL, and those URLs carry `X-Plex-Token` / `apikey`. That text was returned to the page and written to the logs. Access logs also recorded the `?token=` login token that video and live-event URLs use. | Fixed |
| 3 | Low | There was no `.dockerignore`, so `data/` (the database), `.env` and `.git` were sent to the Docker builder. They were never copied into the image. | Fixed |
| 4 | Low | `.gitignore` did not cover stray `*.db` files or `.env.*` variants. | Fixed |
| 5 | Info | The frontend reads no environment variables, and production builds have no source maps. The bundle, the service workers and the manifest contain no secrets or internal hostnames. | Verified; now enforced in config and CI |
| 6 | Info | Every commit on every branch is free of credentials. CI uses only `GITHUB_TOKEN` and prints no secrets. The final image contains only `app/` and the built frontend. | Verified |
| 7 | Info | Media engine settings already hid the TPDB key (`tpdb_api_key_set` only). Push settings expose only the VAPID *public* key, plus a label for each device. | Verified |

## What changed

**Secrets stay on the server.** The new module is `backend/app/services/secret_settings.py`.
- A secret that has been saved comes back as `••••••••`. An empty one comes back empty, so the page can still tell whether it's set.
- `session_secret` and `vapid_private_key` are never sent at all, and cannot be overwritten through the settings API.
- On save, a field that still holds the mask keeps the stored value. Typing a new value replaces it.
- When a Test button sends the mask, the server swaps in the stored value before calling Plex, Jellyfin, Jackett/Prowlarr, TMDB, Trakt or SMTP. All external calls already ran on the server.
- Error messages have secret values and URL credentials removed before they are returned or logged.
- Every log line from every logger, uvicorn's access log included, has `token=`, `apikey=`, `api_key=`, `X-Plex-Token=` and `password=` values masked.

**Settings page.** Focusing a field that holds a saved secret selects it, so typing replaces it. A tooltip explains this.

**Build.**
- `vite.config.ts` sets `sourcemap: false` and `envPrefix: "VITE_"` explicitly.
- `.dockerignore` keeps data, env files, git metadata and tests out of the build context.

**CI.** `scripts/scan-secrets.sh` runs twice on every push and fails the build when it finds something:
- on the tracked source (tests excluded, because they hold deliberate fake secrets)
- on `frontend/dist`, where it also fails if any source map is present

It checks for:
- AWS, GitHub, Slack and Stripe-style keys
- private keys and JWTs
- database URLs that include credentials
- hard-coded `password`, `secret` and `api_key` values

**Tests.** `backend/tests/test_secret_settings.py` covers:
- no seeded secret appears in any settings response
- the mask round-trips on save
- server-only keys can't be overwritten
- Test buttons use the stored key
- URL and log redaction

## Not changed (by design or needs a decision)
- **Local network access needs no login.** Auth applies only to requests that arrive through a Cloudflare tunnel, which is behaviour inherited from AuTorrent. Anyone on the LAN can still *use* the app; they just can't read its secrets any more. Requiring login everywhere would be a separate, opt-in setting.
- **The UI password is stored as plain text** in the database, and login tokens are derived from it. Hashing it (for example with scrypt) would protect it if the database file were copied. It needs a migration and a re-login, so it is proposed as a follow-up.
- **Backup export** (`/api/backup/export`) contains the whole database, secrets included, because that is what a full restore needs. Treat backup files as sensitive.

## How to re-check
```bash
scripts/scan-secrets.sh                                  # source
(cd frontend && npm run build) && scripts/scan-secrets.sh frontend/dist
cd backend && pytest -q tests/test_secret_settings.py
curl -s localhost:4260/api/settings | grep -c '••••••••'   # secrets show as the mask
```

---

# Audit 2 (2026-11-08)

**Scope:** the same as audit 1, plus everything added since: music, Plex sync, ListenBrainz/MusicBrainz, the importer, folder tests and backups.

The method was:
- regex scans of the tracked tree, the git history and a fresh `frontend/dist`
- a check for source maps and `import.meta.env` use
- a review of every route that returns external error text or indexer data
- a new test that calls every GET route with secrets seeded

## Findings

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| 1 | High | `/api/search` sent Jackett/Prowlarr `.torrent` download URLs to the browser. These carry `apikey=<indexer key>` and, for private trackers, the tracker passkey. | Fixed |
| 2 | High | The backup ZIP held every secret in plain text: the database and `settings.json`, including the session secret and the VAPID private key. Anyone on the LAN could download it. | Fixed |
| 3 | Medium | The login was skipped for every request without `CF-Connecting-IP`. A reverse proxy without Cloudflare, or a forwarded port, got full access. | Fixed |
| 4 | Medium | Some routes returned outside error text as-is. For example, a TMDB failure can include its URL with `api_key=`, and the Plex sync status included the error text unscrubbed. | Fixed |
| 5 | Medium | `/api/settings` returned the TPDB key in plain text. It was missing from the masked list; the new GET-route test found it. | Fixed |
| 6 | Info | The source, the git history and the production bundle are free of credentials. There are no source maps. The only build-time variable is `VITE_APP_VERSION`. The importer preview, media settings and push settings mask correctly. | Verified |

## What changed

**Indexer links stay on the server** (`services/link_vault.py`).
- Search results carry `mmlink:<id>` instead of the real link.
- The real link is stored in `indexer_links` for 7 days and is swapped back in by `POST /api/downloads`.
- Info-page links lose any key-like query parameters.

**Encrypted backups** (`services/backup_crypto.py`).
- Export is `POST /api/backup/export {passphrase}`, with a passphrase of at least 8 characters.
- Every secret setting, and every http(s) download link, is replaced with `enc:<Fernet>` in a copy of the database. The key is derived from the passphrase with PBKDF2-HMAC-SHA256 (390,000 rounds, random salt).
- The copy is VACUUMed so old values don't linger in free pages, and `settings.json` is treated the same way.
- Restore needs the passphrase. A wrong one changes nothing. Old plain backups still restore.

**Only the home network skips the login** (`middleware/auth.py` `from_home`).
- A request skips the login only when it has no Cloudflare header, comes from a private or loopback address, and every `X-Forwarded-For` / `X-Real-IP` address it carries is also private.
- `/api/auth/status` follows the same rule.

**Response filter** (`middleware/scrub.py`, the outermost middleware).
- Every `application/json` response under `/api/` has stored secret values (8 characters or more) and URL credentials (`api_key=`, `X-Plex-Token=`, `passkey=` and similar) replaced with the mask.
- Streams and files pass through untouched.
- The list of values is reloaded every 30 seconds and whenever settings are saved.

**CI and tests.**
- `scan-secrets.sh` also flags `?apikey=`, `passkey=` and `X-Plex-Token=` values in URLs.
- New tests:
  - `test_link_vault.py`
  - `test_backup.py` (secrets in backups)
  - `test_auth_home.py`
  - `test_secret_scrub.py`, which includes the every-GET-route sweep with the filter's value list empty, so the routes must mask on their own

## Verification (2026-11-08)
- `pytest`: 1,320 passed.
- `npm run build` succeeds.
- `scripts/scan-secrets.sh` is clean on the source and on a fresh `frontend/dist`, which has no `.map` files.
- The seeded test secrets don't appear in the bundle.
- The git-history scan finds no credentials. The only hit is the deliberately fake, truncated key in `tests/test_secret_settings.py`.
- `shellcheck` passes on all scripts.

## Recommended for the operator
- Download a new encrypted backup.
- Delete old backup ZIPs; they hold the secrets in plain text.
- If an old backup ever left the NAS, rotate:
  - the Plex token
  - the Prowlarr/Jackett API key
  - the qBittorrent password
  - the TMDB and TPDB keys
