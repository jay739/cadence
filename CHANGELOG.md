# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/), versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added

- Phase 1: FastAPI backend with multi-source search (Apple/iTunes, MusicBrainz, Spotify,
  Prowlarr), request/approval/fulfillment flow, Prowlarr-first/Soulseek-fallback fulfillment
  worker, structured per-request fulfillment audit trail, demo mode, and a React web client.
- SSRF-hardened outbound HTTP client, path-traversal-safe library filing, argon2 password
  hashing, JWT sessions, rate limiting on auth and search, CSP and security headers.
- CI (lint, typecheck, unit/integration/e2e tests, build, dependency audit) and a release
  workflow building a multi-arch (amd64/arm64) GHCR image on version tags.
- Admin-editable, database-backed settings for Prowlarr/qBittorrent/slskd/Spotify/library,
  encrypted at rest, with environment variables remaining a working bootstrap/override path.
  A new admin Settings page, backed by masked-read/update/test-connection routes, lets an
  operator configure every integration from the web UI with no restart required.
- Real Alembic migration baseline, replacing the old `create_all()` boot step. A fresh database
  upgrades from nothing to head; a database created before this change is stamped at the
  baseline revision on first boot rather than replayed against tables that already exist.
- Opt-in "Your Spotify" personalization tab: a user can connect their own Spotify account
  (Authorization Code OAuth, read-only scopes) and quick-pick from their saved tracks and
  playlists, additive to the existing app-only search tab and sharing the same app
  registration. Tokens are encrypted at rest with the same key used for other integration
  secrets.
- Dependabot configuration for the Python backend, npm web client, and GitHub Actions.
- Already-in-library check: search results are flagged "In your library" when the track is
  already in Navidrome, so nobody queues a download of something they already own. Needs an
  optional Navidrome service account (`NAVIDROME_ADMIN_USER` / `NAVIDROME_ADMIN_PASSWORD`);
  without it the badges are simply absent and search is untouched. Matching is deliberately
  conservative, since a false "you already have this" is worse than a missed one.
- Profile avatars for everyone: an explicit `avatar_url` still wins, and any account without one
  now gets a deterministic doodle generated server-side as an inline SVG. No new dependency and
  no external avatar service, so it works on an install with no internet access.

No tagged release yet; `v0.1.0` will be cut once the pipeline is proven end to end.
