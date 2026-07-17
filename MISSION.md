# Mission: Cadence

This file is the build command for this project. It is written so it can be handed verbatim
to an autonomous Claude Code session (local or cloud) and produce the same product. It is also
the acceptance contract each phase's output is judged against. Phases are sequential and each
must reach the "Definition of done" bar below before the next one starts.

## Mission statement

Build, polish, verify, and ship Cadence: a self-hostable, single-track music request service.
It is the Overseerr/Ombi pattern (user searches, requests, admin approves, backend fulfills)
applied to individual songs instead of movies, shows, or whole albums/artists. The entire
reason this exists: every existing tool in this space (Lidarr, Ombi, Headphones) is built
around artist/album as the unit of work, and pulls whole discographies once an artist is
monitored. Cadence's non-negotiable premise is that requesting one song never touches, grabs,
or monitors anything beyond that one recording.

Work autonomously, make decisions, record them, and finish each phase. Finish every phase with
an update to REPORT.md at the repo root (append a dated section per phase, do not overwrite
prior phases' entries).

## Operator context (public-safe, never embed any of it)

The operator self-hosts about 40 Docker services behind Nginx Proxy Manager under his own
domain, on one amd64 host (Ubuntu) and one arm64 Oracle A1 VPS, so published Docker images
must be multi-arch (linux/amd64 and linux/arm64). He already runs Prowlarr (indexer
aggregator, proxied) and qBittorrent (behind a VPN container, proxied), both reachable over
his internal Docker network. He tried Ombi as a Lidarr-backed music request tool and it did
not solve this problem, because Ombi's music requests still resolve through Lidarr, which
still pulls full artist/album data once anything is monitored. He has not yet deployed slskd
(Soulseek daemon); when he does, it will need its own dedicated Soulseek account (the network
allows one session per account) and a read-only share of his existing library (some indexers
silently throttle or ban accounts that only leech). Never hardcode his URLs, tokens, indexer
names, or account credentials. All of it is runtime configuration supplied by whoever deploys
the app, documented as environment variables with a .env.example.

His music library spans many languages, most heavily English, Hindi, and Telugu, with
Malayalam, Punjabi, Korean, and others also present. MusicBrainz and Spotify catalog coverage
is materially worse for regional film/devotional music than for English/K-pop; iTunes/Apple's
keyless search API has historically had decent Indian film catalog coverage. Search must
degrade gracefully per source rather than treating any one source as authoritative.

## Product spec

### Phase 1 (build this first, ship it, prove it end to end): Backend + Web client

1. **Multi-source search, presented as tabs**, queried in parallel per request:
   - Apple/iTunes Search API (keyless, no auth, primary source for regional coverage)
   - MusicBrainz (recording search, keyless but rate-limited to 1 req/sec per their policy,
     used for canonical metadata/tagging once a track is picked)
   - Spotify (Client Credentials OAuth flow, app-only, no per-user login required for this
     tab; register one app under the operator's own Spotify developer account)
   - Prowlarr (search across the operator's configured indexers directly via Prowlarr's own
     `/api/v1/search` endpoint; raw release results, filename-derived, not canonical metadata)
   - Every tab must independently succeed, fail, or return empty without blocking the others.
     A single slow or dead source degrades that one tab, never the whole search.
   - A later, explicitly optional Spotify _personalization_ tab: if a user has connected their
     own Spotify account (separate user-level OAuth, Authorization Code flow, opt-in), surface
     their saved tracks/playlists as a quick-pick source. If not connected, that tab shows a
     "Connect Spotify" prompt instead of results. This is additive to, never a replacement for,
     the app-only Spotify search tab above.
2. **Request flow**: user searches, picks the correct result (title, artist, album art,
   source badge shown so duplicates across tabs are distinguishable), submits a request.
   Requesting is idempotent: a second request for a track already pending/fulfilled surfaces
   its existing status instead of creating a duplicate row.
3. **Admin approval queue**: pending requests list, approve or reject with an optional note,
   visible to the requesting user. Approval enqueues fulfillment; rejection is terminal unless
   the user re-requests.
4. **Fulfillment worker**, one track at a time, no artist/album monitoring created anywhere:
   - Try Prowlarr first: search its indexers for the specific track (using the metadata
     captured at request time: title, artist, album, duration if available), evaluate
     candidate releases, grab the best single-track match via Prowlarr's grab API, hand off to
     the operator's existing qBittorrent instance, poll for completion.
   - If Prowlarr yields nothing usable within a bounded attempt budget, fall back to Soulseek
     via slskd's API: search, evaluate candidates (bitrate, format, exact filename plausibility
     against the requested title/artist), download the single file, poll for completion.
   - On completion: move/rename the file into the operator's library layout (configurable
     target path pattern, e.g. `{Artist}/{Title}.{ext}`), tag it using the metadata captured
     at request time (prefer MusicBrainz canonical tags when available), mark the request
     fulfilled with the final file path recorded.
   - On exhaustion of both paths: mark the request failed with a reason, visible to the admin
     and to the requesting user, no silent disappearance.
   - Every fulfillment attempt (source tried, query used, candidates considered, one chosen and
     why, or why none qualified) is logged in a structured, retrievable-per-request audit trail,
     not just application logs.
5. **User accounts**: registration/login for requesting users, a separate admin role for the
   approval queue and system configuration (indexer/slskd connection settings, source API
   keys, fulfillment attempt budgets). Single admin is sufficient; do not build multi-tenant
   organizations or SSO.
6. **Request history and statuses** visible to each user for their own requests: pending,
   approved (queued), downloading, fulfilled (with a way to know where the file landed or get
   it, appropriate to a self-hosted single-user-household context, not a distribution feature),
   rejected, failed.
7. **Demo mode**: a flag that seeds realistic fake search results and a fake request queue with
   every status represented, so the UI, screenshots, and first-run evaluation need zero real
   external API keys or a real Prowlarr/slskd instance.

Explicitly out of scope for Phase 1: any of the native clients (Phase 2+), bulk/album-level
requests of any kind (this is the one thing Cadence must never grow, even as a convenience
feature), multi-admin roles, public unauthenticated access.

### Phase 2+ (separate, later efforts, each its own milestone against the Phase 1 API)

Once Phase 1's API is stable and versioned, native clients are built against it as independent
projects, each using its platform's first-party toolkit so each feels genuinely native rather
than a shared cross-platform shell:

- Windows: WinUI 3 / .NET
- macOS: SwiftUI
- iOS: SwiftUI
- Android: Kotlin + Jetpack Compose

Each native client phase gets its own MISSION.md derived from this one plus the finished
Phase 1 OpenAPI contract, written once Phase 1 ships. Do not start any of these until Phase 1
has passed its full verification pass below and produced a real, stable OpenAPI spec.

## Architecture and security (non-negotiable, Phase 1)

- **Backend**: Python, FastAPI, Pydantic v2 models for every request/response so the OpenAPI
  spec generated at `/openapi.json` is accurate and complete enough for later client codegen.
  SQLite for Phase 1 (matches the operator's other self-hosted single-node services; document
  the upgrade path to Postgres in REPORT.md if load ever warranted it, do not build it now).
- **Background jobs**: the fulfillment worker runs as an async task queue in-process (e.g.
  arq or a simple asyncio-based worker) backed by the same SQLite database for job state, so
  the whole stack is one deployable unit with no separate Redis/broker dependency for Phase 1.
- **Web client**: a single-page app (React or SvelteKit, pick one and justify it in REPORT.md)
  served by the same container, calling the FastAPI backend over its own origin behind the
  operator's existing reverse proxy.
- **Configuration via environment only**: DATABASE_URL/DATA_DIR, SESSION_SECRET (generated on
  first boot if unset, persisted with owner-only permissions), ADMIN_BOOTSTRAP_EMAIL,
  PROWLARR_URL, PROWLARR_API_KEY, QBITTORRENT_URL, QBITTORRENT_USERNAME,
  QBITTORRENT_PASSWORD, SLSKD_URL, SLSKD_API_KEY (all optional at boot, feature-detected;
  Cadence must start and serve search/browsing even if fulfillment backends are unconfigured,
  clearly surfacing "not configured" rather than failing to boot), SPOTIFY_CLIENT_ID,
  SPOTIFY_CLIENT_SECRET, MUSIC_LIBRARY_PATH, DEMO_MODE. Provide .env.example. A committed
  secret of any kind is a failed run.
- User passwords hashed with a modern algorithm (argon2 or bcrypt), never logged. Session
  cookies HMAC-signed or JWT with a real expiry and rotation on privilege change. Login is
  rate-limited. Login/session comparison paths are constant-time where relevant.
- All third-party API keys (Spotify, Prowlarr, slskd) live only in backend config, never sent
  to or callable directly from the browser; the web client only ever talks to Cadence's own
  API, which proxies and rate-limits outbound calls to each source.
- Strict CSP, X-Content-Type-Options, Referrer-Policy, frame-ancestors none on every response.
- Docker: multi-stage build, non-root user, pinned base images, HEALTHCHECK against
  `/api/health`, SQLite and downloaded-file staging area on named volumes. docker-compose.yml
  provided, including commented-out example wiring for Prowlarr/qBittorrent/slskd service
  names as they would appear on the operator's own Docker network.
- `/api/health` returns version, mode, database-ok, and per-integration configured/reachable
  booleans (Prowlarr, qBittorrent, slskd, Spotify) without leaking URLs, keys, or paths.

## Error handling bar

No unhandled path anywhere. Every route validates input, handles auth failures and database
errors with typed client responses and detailed server-side logs. Every external API call
(Apple, MusicBrainz, Spotify, Prowlarr, slskd, qBittorrent) has an explicit timeout, a bounded
retry policy, and a failure mode that degrades that one feature rather than the request or the
process. The fulfillment worker must survive a crash mid-download: on restart, in-flight jobs
are resumed or re-evaluated from persisted state, never silently lost or double-grabbed.
Unhandled exceptions in the worker terminate that job loudly (marked failed, logged) and never
take down the worker loop itself.

## Testing

- Unit: search-result normalization per source (mapping Apple/MusicBrainz/Spotify/Prowlarr's
  very different response shapes into one internal model), fulfillment candidate scoring,
  idempotent-request logic, password hashing/session logic, tag-writing.
- Integration: every route against a real SQLite test database; search endpoints against
  recorded/mocked upstream responses (happy path and every error class: timeout, malformed
  JSON, empty results, auth failure); fulfillment worker against a mocked Prowlarr/slskd/
  qBittorrent so the Prowlarr-then-Soulseek-fallback logic is provably correct without needing
  live services in CI.
- End-to-end: Playwright (or equivalent) against demo mode covering search across tabs,
  submitting a request, admin approval, watching a demo fulfillment complete, request history.
- Everything runs in CI; the run is red if any of it fails.

## CI/CD and releases (semver-driven)

- ci.yml on every push and PR to main: install from lockfile, lint, typecheck, unit and
  integration tests, end-to-end tests, production build, dependency audit failing on high or
  critical advisories.
- release.yml on tags matching v*.*.\*: full validation, then a multi-arch image
  (linux/amd64, linux/arm64) pushed to GHCR tagged :vX.Y.Z and :latest, then a GitHub Release
  with generated notes. Conventional commits throughout, CHANGELOG.md maintained, first
  release v0.1.0 tagged when the pipeline is proven end to end.

## Docs

README.md: pitch (what problem this solves and why existing \*arr tools do not), screenshots
from demo mode, quickstart compose, full configuration reference including how to obtain a
Spotify Client Credentials app and a Prowlarr API key, reverse proxy notes, security posture
section, development guide. CONTRIBUTING.md and a runbook covering common operational issues
(a fulfillment source misconfigured, a stuck job, how to re-run a failed request). MIT license
in the name of Jayakrishna Konda. All prose in every file avoids em dashes and double hyphens;
use commas, conjunctions, or separate sentences.

Apply the operator's standard repo lifecycle baseline once the app is functional: GitHub issue
and PR templates, Discussions enabled with a basic category setup, docs/PACKAGES.md cataloguing
every dependency (generated, gitignored, never pushed), a security baseline pass appropriate to
a self-hosted service handling third-party API keys and a download pipeline.

## Git conduct (strict)

Author every commit as Jayakrishna Konda <contact@jay739.dev>. Never add AI attribution of any
kind anywhere: no Co-Authored-By trailers, no generated-with lines, nothing in commits, docs,
or code comments. Small, focused, conventionally named commits. Do not push; the operator
pushes.

## Final verification and adversarial pass (end of Phase 1)

1. Full test suite green, production build clean, audits clean.
2. Run the production build in demo mode and exercise the whole flow end to end: search across
   every tab, submit a request, approve it as admin, observe demo fulfillment, check history.
3. A dedicated adversarial review of the whole codebase hunting specifically: auth bypass on
   admin routes (especially the approval queue and integration config), SSRF via any
   user-influenced URL passed to an outbound fetch (search queries reaching Apple/MusicBrainz/
   Spotify/Prowlarr must be strictly parameterized, never raw-interpolated), path traversal in
   the file-move/tag-writing step (a maliciously crafted title/artist must never escape
   MUSIC_LIBRARY_PATH), secrets or API keys anywhere in the repo or logs, timing leaks in
   password/session comparison, rate-limit gaps on search and auth endpoints, CSP holes,
   dependency confusion in the multi-source client code. Fix findings, re-run the suite.
4. A documented failure in REPORT.md is acceptable; a hidden one is not.

## REPORT.md

Append one dated section per phase (never overwrite a previous phase's section). Each section
covers: what was built and why this shape; stack justification (backend framework, web
framework, job queue approach); every route with its validation, auth, and rate limit; the
full search-source integration matrix (source, auth method, rate limit respected, failure
mode); the fulfillment decision logic (how Prowlarr candidates are scored, when the Soulseek
fallback triggers, how ties/ambiguous matches are handled); security checklist with honest
covered and not-covered status; test coverage summary; dependency list with justifications;
deployment steps for the operator's stack (compose snippet, reverse proxy, environment
reference); known gaps ranked by risk; the next three things worth doing; anything attempted
and abandoned, with reasons.
