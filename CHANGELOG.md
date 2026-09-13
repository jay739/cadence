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
- Richer tags on every filed track, and tags at all on the ones that had none. Tagging now
  handles Opus and MP4/M4A alongside MP3 and FLAC: Opus is what the YouTube tier lands, so the
  regional catalogue that only that tier can find was arriving in the library completely
  untagged. Beyond title/artist/album, a track now carries the release year, every credited
  artist (so featured performers survive), the album/soundtrack it came from, genre, release
  country and language, plus embedded front-cover artwork fetched from the Cover Art Archive
  and capped at 2MB. Year, credits, genre, country and language come from MusicBrainz, so they
  are written only for a request that carried a recording MBID, and any field MusicBrainz does
  not know is left unwritten rather than guessed.

### Changed

- One search box instead of two. The separate "paste a Spotify or Apple Music link" input is
  gone: the main search box now decides for itself, resolving anything that looks like a link
  to one exact track and searching everything else across the four sources as before. A link is
  only ever looked up when it is pasted whole or submitted, never as-you-type, since every
  prefix of a URL is an unrecognized link; a link to an unsupported platform still goes to the
  resolver, so its specific rejection is what the user reads rather than four sources finding
  nothing for a URL.
- The "which source wins" env/database choice has moved out of every integration's permanent
  layout. It is asked once, during first-run setup, and applies to every integration; afterwards
  each section keeps its own copy behind a collapsed "Advanced" disclosure whose summary still
  names the source that integration obeys. An install with `SETTINGS_FORCE_ENV_PRECEDENCE` set
  opens that disclosure instead of collapsing it, so a stored choice that isn't in effect is
  never hidden. The endpoint and its semantics are unchanged.
- A request now asks for a quality (`any` / `lossy` / `lossless`) instead of naming a container
  (`any` / `mp3` / `flac`). Naming a container was a promise Cadence could not keep: a request
  for FLAC was fulfilled with an Opus file, because the format preference only reordered
  candidates and the YouTube tier ignored it entirely. Lossless is now a real filter, the
  YouTube tier declines a lossless request outright (YouTube has no lossless source), and a
  request that finds only lossy copies ends in a new `awaiting_quality` status that asks the
  requester whether to accept one rather than silently downgrading or failing. Existing rows are
  migrated (`flac` to `lossless`, `mp3` to `lossy`).

No tagged release yet; `v0.1.0` will be cut once the pipeline is proven end to end.
