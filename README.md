# Cadence

<p align="center">
  <img src="docs/logo.svg" alt="Cadence logo" width="128" />
</p>

A self-hosted, single-track music request service. Search for one song, request it, an admin
approves it, and Cadence fetches exactly that track, nothing else.

Every other tool in this space (Lidarr, Ombi, Headphones) is built around the artist or album
as the unit of work: monitor an artist and you get their whole discography. Ombi's music
requests still resolve through Lidarr under the hood, so even Ombi ends up pulling full
artist/album data once anything is monitored. Cadence exists because sometimes you just want
one song, and nothing else should get touched.

## What it does

- Search for a track across four sources at once, shown as separate tabs so you can pick the
  right result with correct art and tell duplicates apart by source badge:
  - **Apple/iTunes** (keyless, strong regional/film catalog coverage)
  - **MusicBrainz** (keyless, canonical metadata for tagging once a track is picked)
  - **Spotify** (your own Client Credentials app, no per-user login needed)
  - **Prowlarr** (your own configured indexers, raw releases)
- Request it. Requesting an already-pending or already-fulfilled track just shows its current
  status instead of creating a duplicate.
- An admin approves or rejects from a queue, with an optional note the requester sees.
- An admin can also wire up Prowlarr/qBittorrent/slskd/Spotify and the library layout entirely
  from a Settings page in the web UI, test each connection before saving, and have it take
  effect immediately, no SSH access or `.env` editing required. Secrets are encrypted at rest.
- On approval, Cadence tries your Prowlarr indexers first, grabs the best single-track match,
  and hands it to your existing qBittorrent. If nothing suitable turns up, it falls back to
  Soulseek via slskd. Either way, only the one requested file is ever touched.
- Every fulfillment attempt (what was searched, what was considered, what was picked and why,
  or why nothing qualified) is recorded per request and viewable in the UI, not just buried in
  server logs.
- No album, no artist, no library scan, no monitoring of anything beyond the one recording you
  asked for.

## Screenshots

Run with `DEMO_MODE=true` (see Quickstart below) to explore the full UI, including every
request status, with zero real API keys or a live Prowlarr/slskd instance.

## Quickstart (Docker Compose)

```bash
git clone https://github.com/jay739/cadence.git
cd cadence
cp .env.example .env
# edit .env: at minimum set ADMIN_BOOTSTRAP_EMAIL / ADMIN_BOOTSTRAP_PASSWORD,
# or leave DEMO_MODE=true to explore without any configuration at all.
docker compose up -d
```

Cadence listens on `:8000`. Put it behind your existing reverse proxy for TLS and a real
hostname; Cadence itself does not terminate TLS.

## Configuration reference

All configuration is environment variables; see [`.env.example`](.env.example) for the full,
commented list. Every third-party integration is optional at boot: Cadence starts and serves
search/browsing even with none of them configured, surfacing "not configured" rather than
failing to boot. Nothing here is ever sent to or callable from the browser; the web client
only ever talks to Cadence's own API.

The Prowlarr/qBittorrent/slskd/Spotify/library rows below can also be set, or changed, from the
admin Settings page in the web UI instead of editing `.env`, with no restart needed. An
environment variable, when set, always takes precedence over a value saved through the UI, so
a docker-compose deployment that only ever uses env vars keeps working exactly as before.

| Variable                                                            | Purpose                                                                                                                                                                                                                                                                                                         |
| ------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `DEMO_MODE`                                                         | `true` seeds fake search results and a fake request queue, zero external calls.                                                                                                                                                                                                                                 |
| `DATABASE_URL` / `DATA_DIR`                                         | Where the SQLite database and downloaded-file staging area live.                                                                                                                                                                                                                                                |
| `SESSION_SECRET`                                                    | HMAC/JWT signing key. Auto-generated and persisted (owner-only permissions) on first boot if unset.                                                                                                                                                                                                             |
| `ADMIN_BOOTSTRAP_EMAIL` / `ADMIN_BOOTSTRAP_PASSWORD`                | Creates the single admin account on first boot.                                                                                                                                                                                                                                                                 |
| `PROWLARR_URL` / `PROWLARR_API_KEY`                                 | Your Prowlarr instance. Get an API key from Prowlarr's Settings -> General.                                                                                                                                                                                                                                     |
| `QBITTORRENT_URL` / `QBITTORRENT_USERNAME` / `QBITTORRENT_PASSWORD` | Your existing qBittorrent instance (used to poll grabbed releases for completion).                                                                                                                                                                                                                              |
| `SLSKD_URL` / `SLSKD_API_KEY`                                       | Your slskd (Soulseek daemon) instance, the fallback path.                                                                                                                                                                                                                                                       |
| `NAVIDROME_URL`                                                     | Lets library users sign in with the Navidrome credentials they already have. Local accounts still work and are tried first, so a Navidrome outage never locks you out.                                                                                                                                          |
| `NAVIDROME_ADMIN_USER` / `NAVIDROME_ADMIN_PASSWORD`                 | Optional Navidrome service account used only to flag tracks that are **already in your library**, so nobody queues a pointless download. An ordinary read-only account is enough. Unset means the badges never appear; nothing else changes.                                                                    |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`                       | A Spotify app from [developer.spotify.com](https://developer.spotify.com/dashboard). Powers the app-only Spotify search tab (Client Credentials, no redirect URI needed) and, if `APP_BASE_URL` is also set, the opt-in "Your Spotify" personalization tab below.                                               |
| `APP_BASE_URL`                                                      | Required only for the opt-in "Your Spotify" tab (a user connects their own account via Authorization Code OAuth to quick-pick from their saved tracks/playlists). Builds the OAuth redirect URI (`<APP_BASE_URL>/api/spotify/callback`); must match a Redirect URI registered on the same Spotify app as above. |
| `MUSIC_LIBRARY_PATH` / `LIBRARY_PATH_PATTERN`                       | Where fulfilled tracks are filed, and the naming pattern (default `{artist}/{title}.{ext}`).                                                                                                                                                                                                                    |

## Reverse proxy notes

Cadence expects to sit behind a reverse proxy (Nginx Proxy Manager, Caddy, Traefik, etc.) that
terminates TLS and forwards to `:8000`. It sends its own security headers (CSP,
`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options: DENY`); avoid a proxy config that
overrides or strips them. If you serve the API and web client from the same origin (the
default, since one container serves both), no CORS configuration is needed.

## Backing up the database

The SQLite database runs in WAL mode (better concurrency between web requests and the
background download worker). While the app is running, recent commits can live in
`cadence.db-wal` alongside `cadence.db` before being checkpointed back into the main file, so
**copying only `cadence.db` while the container is running can miss data**. Either stop the
container first, or use `sqlite3 cadence.db "VACUUM INTO '/backup/path/cadence-backup.db'"`
(safe to run against a live database; it checkpoints WAL into a fresh, complete file at the
target path).

## Security posture

- Passwords hashed with argon2; sessions are JWTs with real expiry and a token-version claim
  that invalidates all prior sessions on rotation.
- Login and search endpoints are rate-limited.
- All outbound calls to Apple/MusicBrainz/Spotify/Prowlarr/slskd/qBittorrent go through an
  SSRF-hardened client that resolves DNS itself and blocks link-local, loopback, and metadata
  addresses (with a separate, narrower allowance for your own operator-configured
  Prowlarr/qBittorrent/slskd, which legitimately live on a private network).
- The library file-move/tag-writing step sanitizes every path component and verifies the final
  resolved path never escapes `MUSIC_LIBRARY_PATH`, even under adversarial titles/artists.
- No secret is ever committed to this repository; see `.env.example`.
- Integration secrets saved through the admin Settings page are encrypted at rest in the
  SQLite database (never plaintext) using a key generated on first boot and persisted with
  owner-only permissions, the same pattern `SESSION_SECRET` uses. The settings API masks every
  secret in its responses (e.g. `••••••••ab12`), and admin-only routes guard both reading and
  updating them.
- To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Development guide

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full dev setup (backend, web client, and
end-to-end tests) and coding conventions.

## Status

Phase 1 (backend + web client) is built; see [CHANGELOG.md](CHANGELOG.md) for what shipped in
each release. Native apps (Windows, macOS, iOS, Android) are later,
separate phases, each built against Phase 1's stable, OpenAPI-documented API.

## License

MIT, see [LICENSE](LICENSE).
