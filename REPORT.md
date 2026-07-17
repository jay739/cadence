# REPORT

This file is appended to, never overwritten, once per phase. This is Phase 1's entry.

## Phase 1: Backend + Web Client (2026-07-17)

### What was built and why this shape

A FastAPI backend and a React web client, deployed as one container, implementing the full
Phase 1 product spec from MISSION.md: multi-source search across four sources shown as tabs,
an idempotent request flow, an admin approval queue, a Prowlarr-first/Soulseek-fallback
fulfillment worker with a structured per-request audit trail, demo mode, and the full security
bar (SSRF protection, path-traversal protection, argon2 hashing, JWT sessions, rate limiting,
CSP). Everything lives under `backend/` and `web/`, sharing the repo root's Docker/CI tooling.

The shape follows the Overseerr/Ombi request-approval-fulfillment pattern the mission asks for,
but with one hard constraint threaded through every layer: the `Request` model has no
album/artist equivalent anywhere in the schema, the fulfillment worker only ever searches for
and grabs the one track named on the request, and the path-safety module only ever files a
single resolved file path. There is no code path in this codebase capable of monitoring or
bulk-grabbing anything.

### Stack justification

- **Backend framework: FastAPI.** Mission-specified. Async-native, and Pydantic v2 models used
  for every request/response give an accurate, complete `/openapi.json` for free, which the
  mission calls out as needed for later native-client codegen.
- **Database: SQLite via SQLAlchemy 2.0 async ORM + aiosqlite.** Mission-specified for Phase 1.
  Chose SQLAlchemy's async ORM over raw `aiosqlite`/`databases` for the mapped models, migration
  path (Alembic is installed but not yet wired to a migration, since `create_all()` is
  sufficient pre-v1; noted as a gap below), and because the async session pattern composes
  cleanly with FastAPI's dependency injection.
- **Job queue: in-process asyncio worker, no Redis/broker.** Mission-specified ("no separate
  Redis/broker dependency for Phase 1"). `FulfillmentWorker` polls the `requests` table for rows
  in `APPROVED` status, claims one at a time with an optimistic lock token, and drives it
  through `app.services.fulfillment`. Job state lives entirely in the database, so the whole
  stack really is one deployable unit. Considered `arq` (mentioned as an option in the mission)
  but it wants its own Redis-backed queue; a bare asyncio loop over the existing SQLite table
  meets every requirement (crash recovery, single active job, wake-on-approval via an
  `asyncio.Event`) with zero new infrastructure.
- **Web framework: React + TypeScript + Vite, not SvelteKit.** Both were explicitly left open
  by the mission. Chose React for: (1) the broadest pool of contributors likely to touch a
  self-hosted OSS project already knowing it; (2) TanStack Query's request/response caching and
  polling primitives being an exact fit for the "poll a downloading request until it resolves"
  UI need, with a more mature ecosystem than SvelteKit's equivalents; (3) React Router's
  well-trodden nested-route + loader patterns mapping cleanly onto the five-page app
  (search/requests/detail/admin/auth) with clean auth-guarding via a wrapper component.
- **Rate limiting: slowapi.** A thin wrapper over the `limits` library giving a decorator-based
  API (`@limiter.limit(...)`) that composes with FastAPI's dependency/exception-handler model
  without a separate proxy-layer rate limiter, appropriate for a single-container self-hosted
  app with no separate edge/gateway layer.
- **Password hashing: argon2 (via passlib).** Mission-specified as one of the two acceptable
  algorithms; argon2 chosen over bcrypt for its memory-hardness against GPU-accelerated
  cracking, at the cost of a slightly heavier dependency (`argon2-cffi`).
- **HTTP client: httpx**, wrapped in a custom `SafeHttpClient` (see security section) rather
  than used directly anywhere in the codebase, so every outbound call is provably covered by
  the same SSRF policy without relying on each adapter to remember to apply it.

### Every route, its validation, auth, and rate limit

| Method & path                             | Auth                                     | Rate limit    | Validation                                                                                                                                                                                                                                  |
| ----------------------------------------- | ---------------------------------------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/health`                         | none (public)                            | none          | n/a; never leaks URLs/keys/paths, only configured/reachable booleans                                                                                                                                                                        |
| `POST /api/auth/register`                 | none                                     | 10/min per IP | Pydantic `RegisterIn`: `EmailStr`, password min 10 chars                                                                                                                                                                                    |
| `POST /api/auth/login`                    | none                                     | 10/min per IP | Pydantic `LoginIn`; constant-time-safe (dummy-hash verify on unknown email so timing doesn't reveal account existence)                                                                                                                      |
| `POST /api/auth/logout`                   | none required (clears cookie either way) | none          | n/a                                                                                                                                                                                                                                         |
| `GET /api/auth/me`                        | Bearer token or session cookie           | none          | n/a                                                                                                                                                                                                                                         |
| `GET /api/search`                         | required (any authenticated user)        | 30/min per IP | `q`: 1-300 chars; `limit`: 1-50                                                                                                                                                                                                             |
| `POST /api/requests`                      | required                                 | 20/min per IP | `CreateRequestIn`: source is an enum of the four allowed values, title/artist required non-empty (whitespace-stripped), `musicbrainz_recording_id` validated as a strict MBID/UUID (see security findings), all string fields length-capped |
| `GET /api/requests/mine`                  | required                                 | none          | n/a                                                                                                                                                                                                                                         |
| `GET /api/requests/{request_id}`          | required, owner-or-admin                 | none          | 404 (not 403) for a non-owner, so existence isn't leaked to other users                                                                                                                                                                     |
| `GET /api/requests/admin/pending`         | admin only                               | none          | n/a                                                                                                                                                                                                                                         |
| `GET /api/requests/admin/all`             | admin only                               | none          | n/a                                                                                                                                                                                                                                         |
| `POST /api/requests/{request_id}/approve` | admin only                               | none          | `ApproveRequestIn`: optional note, max 2000 chars                                                                                                                                                                                           |
| `POST /api/requests/{request_id}/reject`  | admin only                               | none          | `RejectRequestIn`: same                                                                                                                                                                                                                     |

Every route funnels database errors and unhandled exceptions through the app-level exception
handlers (`SQLAlchemyError` -> 500 with a generic message, anything else -> 500 with a generic
message, both logged server-side with full detail); a `RequestValidationError` handler passes
Pydantic's error list through `jsonable_encoder` (a real bug this caught and fixed: the raw
error objects aren't always JSON-serializable, see findings below).

### Search-source integration matrix

| Source       | Auth method                                                                               | Rate limit respected                                                                                     | Failure mode                                                                                                                                              |
| ------------ | ----------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Apple/iTunes | none (keyless)                                                                            | none published; not throttled client-side beyond the shared 30/min per-user search limit                 | Timeout/5xx/malformed JSON caught in `SearchSource.run()`, reported as `ok: false` with an error string; other tabs unaffected                            |
| MusicBrainz  | none (keyless)                                                                            | 1 req/sec, enforced by a process-wide `asyncio.Lock`-based limiter shared across all concurrent searches | Same isolation as above; the limiter adds latency, never a failure, under load                                                                            |
| Spotify      | Client Credentials OAuth (app-only); token cached in-process, refreshed 30s before expiry | Spotify's own per-app limits, not separately throttled client-side                                       | Token-fetch failure or search failure both isolated the same way; reports "Not configured" distinctly from a live failure when no client ID/secret is set |
| Prowlarr     | operator API key via `X-Api-Key` header                                                   | none (operator's own instance)                                                                           | Same isolation; "Not configured" when no URL/key set                                                                                                      |

All four run via `asyncio.gather(..., return_exceptions=True)` in
`app.services.search.multi_source_search`, with each adapter's own `SearchSource.run()` already
catching its own errors as a first line of defense and the outer `gather` as a second line in
case an adapter has a bug that raises during normalization.

### Fulfillment decision logic

1. **Prowlarr attempt**: search `/api/v1/search` with `"{artist} {title}"` as the query, audio
   category IDs. Every raw release is scored via `app.services.scoring`: a `difflib`-based fuzzy
   similarity between the release's filename and both `"{artist} {title}"` and
   `"{title} {artist}"` orderings (whichever scores higher), plus a bonus if the exact title
   substring appears, plus a format-quality signal (FLAC/ALAC > WAV > MP3/AAC > OGG/M4A) and
   file size as tertiary tie-breakers. A candidate must score >= 0.45 similarity to qualify at
   all (chosen empirically: high enough to reject clearly unrelated releases, low enough to
   tolerate messy real-world filenames like `01 - Artist_Title (Official).mp3`). The
   highest-scoring qualifying candidate is grabbed via Prowlarr's own `/api/v1/search` POST
   (grab) endpoint, using its `indexerId`. Every step (search, scoring, grab attempt) is logged
   as a `FulfillmentAttempt` row regardless of outcome.
2. **qBittorrent poll**: once grabbed, Cadence polls qBittorrent (find the torrent by name hint,
   check progress, list files, pick the largest file in the torrent as the audio file) rather
   than submitting the download itself, since Prowlarr hands the release directly to
   qBittorrent (the operator's existing download client) on grab.
3. **Soulseek fallback**: triggers whenever Prowlarr's path returns `False` for any reason
   (not configured, no candidates, no qualifying candidate, grab failure, or qBittorrent
   never reporting completion within that fulfillment pass). Searches slskd, applies the same
   scoring function against filenames (so a single scoring implementation, unit-tested in
   isolation, governs both backends' candidate selection), and downloads the winning file.
4. **Ties/ambiguous matches**: resolved deterministically by the sort key
   `(-similarity, -format_score, -size_bytes)`, so the same input always produces the same
   winner; there is no randomness or first-match behavior anywhere in the scoring path.
5. **Exhaustion**: if both backends fail to produce a fulfilled file, the request is marked
   `FAILED` with a human-readable reason, visible to both the admin and the requesting user.
   Nothing disappears silently.
6. **Crash recovery**: `FulfillmentWorker.recover_crashed_jobs()` runs once at worker startup,
   resetting any request stuck in `DOWNLOADING` (meaning a previous process died mid-job) back
   to `APPROVED`. Recovery re-evaluates from scratch (a fresh search) rather than trying to
   resume an in-flight download, a deliberate simplification: Soulseek/Prowlarr downloads are
   cheap to re-initiate, and re-scoring guards against double-grabbing something already
   fulfilled by checking `fulfill_request`'s early-return for already-`FULFILLED` requests.

### Security checklist (honest)

**Covered:**

- SSRF protection: every outbound call (Apple, MusicBrainz, Spotify, Prowlarr, qBittorrent,
  slskd) goes through `SafeHttpClient`, which resolves DNS itself and blocks link-local,
  loopback, multicast, and cloud-metadata addresses before ever opening a connection, re-checked
  on every retry (not just once) to close a DNS-rebinding race. Public sources use a strict
  policy that also blocks RFC1918 private ranges; operator-configured integrations
  (Prowlarr/qBittorrent/slskd) use a narrower policy that permits RFC1918 (since they
  legitimately live on the operator's own Docker network) but still blocks the metadata address
  unconditionally. Verified by adversarial testing directly against the module.
- Path traversal: `app.services.pathsafety.render_library_path` sanitizes every path component
  independently (strips separators, NUL/control characters, leading dots, wildcard characters,
  truncates length) and then verifies the final _resolved_ path is still inside the resolved
  library root before any filesystem write, as defense in depth on top of the component-level
  sanitization. Adversarially tested against `../../../etc/passwd`, absolute paths, NUL-byte
  injection, Windows-style paths, and 10,000-character inputs; every case stayed inside the
  library root.
- Auth bypass: every admin-prefixed route requires `require_admin`; request-detail enforces
  owner-or-admin and returns 404 (not 403) to a non-owner so existence isn't leaked. Verified
  the `/{request_id}` catch-all route pattern cannot shadow the two-segment `/admin/*` routes
  (Starlette's path converter only captures a single segment for `{request_id}`).
- Secrets: no secret or API key exists in the repository, logs, or error messages. Verified
  every API key is transmitted via HTTP headers, never URL query strings, so even a logged
  `SafeHttpError` (which includes the target URL) cannot leak one. Verified httpx's own
  exception messages never echo header values.
- Password hashing: argon2, never logged; login timing does not distinguish "unknown email"
  from "wrong password" (a dummy-hash verify runs on the unknown-email path too).
- Session tokens: JWT (HS256) with real expiry and a `token_version` claim; rotating a user's
  `token_version` invalidates every previously issued token.
- Rate limiting: auth (register/login) and search are both rate-limited; request creation is
  rate-limited too (not explicitly required by the mission but a reasonable extension, since
  fulfillment work is triggered downstream of it).
- CSP: `default-src 'self'`, `script-src 'self'` (no inline scripts, no `unsafe-inline` for JS),
  `frame-ancestors 'none'`, plus `X-Content-Type-Options: nosniff`, `Referrer-Policy:
no-referrer`, `X-Frame-Options: DENY`, `Permissions-Policy` denying geolocation/mic/camera, on
  every response via middleware.
- Dependency confusion: every keyless/OAuth third-party base URL (Apple, MusicBrainz, Spotify)
  is a hardcoded module-level constant, never derived from configuration or user input, so
  there is no path by which a search query or environment variable could redirect those calls
  elsewhere.
- Dependency audit: `pip-audit` and `npm audit` both clean as of this report (a pytest CVE,
  PYSEC-2026-1845, was found and fixed by bumping to the patched 9.0.3+ line).

**Found and fixed during this pass (see git history for full detail):**

- `musicbrainz_recording_id` was accepted from the client with only a length cap, then
  raw-interpolated into the MusicBrainz lookup URL path in
  `app.search_sources.musicbrainz.lookup_recording`. A crafted value such as
  `"../../v2/artist/x"` would have redirected that outbound request to an arbitrary
  MusicBrainz API path (never an internal address, since the hostname itself is hardcoded, but
  still a real input-validation gap). Fixed at both the Pydantic input boundary (a strict
  MBID/UUID regex validator) and independently at `lookup_recording`'s point of use, so a
  future call site that bypasses the schema layer is still protected. Covered by
  `tests/unit/test_musicbrainz_recording_id_validation.py`.
- The backend's static-file mount only served `index.html` for the literal root path, so any
  direct navigation or refresh on a React Router path (`/login`, `/search`,
  `/requests/<id>`, `/admin`) 404'd instead of loading the SPA. Not a security bug, but found
  via the same adversarial-minded verification pass (the Playwright e2e suite caught it
  immediately, failing at the very first navigation in every test). Fixed with an explicit
  `/assets` mount plus a catch-all route serving the matching static file when one exists and
  falling back to `index.html` otherwise, with an explicit guard so an unknown `/api/*` path
  still 404s as JSON instead of silently serving HTML, plus a resolved-path containment check
  on the requested path as defense in depth.
- The Soulseek completion check compared `state.lower().endswith("completed")`, which never
  matches real slskd's composite state strings like `"Completed, Succeeded"` (vs.
  `"Completed, Errored"`) -- found via integration testing with realistic mocked slskd
  responses, not by inspection. Fixed to check both `"completed"` and `"succeeded"` appear in
  the state string, so a genuinely completed-but-errored transfer is correctly still not
  treated as fulfilled.
- The multi-stage Docker build failed entirely: `backend/pyproject.toml` declared
  `readme = "../README.md"`, which resolves fine when installing from the monorepo directly
  (the file exists one directory up from `backend/`) but not inside the Dockerfile's
  `backend-builder` stage, which only copies `backend/pyproject.toml` and `backend/app` into
  the build context, so hatchling's metadata validation failed on a missing file mid-build.
  Found by actually building the image (`docker build .`) rather than trusting that source
  running locally implied the packaged build would work too. Fixed by dropping the `readme`
  field; verified afterward with a full build, a container run against `/api/health`, a
  successful `docker` `HEALTHCHECK` transition to `healthy`, and confirmation the process runs
  as the intended non-root `cadence` (uid 10001) user.

**Not covered / accepted low-risk gaps:**

- MusicBrainz Lucene query syntax is built from the user's raw search term
  (`f'recording:"{query}"'`) and passed as a `params=` value, so it cannot escape the URL or
  cause SSRF/injection against the HTTP request itself, but a user could craft Lucene operators
  (e.g. `" OR recording:*`) to broaden their own search results. This only affects what that
  same user sees in their own search tab; no other user's data or any write path is affected.
  Not fixed, since escaping quotes would break legitimate searches for titles containing them,
  and the blast radius is limited to the requester's own read-only search view.
- `constant_time_compare()` in `app.core.security` is implemented and unit-tested but not
  currently called by any application code path (password verification goes through passlib's
  own constant-time compare; session tokens are JWT-signature-verified, which is already
  timing-safe). Left in place as available utility for any future raw-secret comparison need,
  documented honestly rather than presented as protecting something it doesn't currently touch.
- The session cookie (`cadence_session`, set alongside the Bearer token on login) uses
  `SameSite=Lax`, which blocks the common cross-site POST/fetch CSRF vectors but not a
  cross-site top-level GET navigation that itself triggers a state change. No route in this
  API performs a state change on GET, so this does not currently matter, but if a future route
  violated that convention the cookie path alone would not protect it. The web client
  authenticates exclusively via the `Authorization: Bearer` header (stored client-side, sent
  explicitly), so the cookie is a secondary/legacy-compatible path, not the client's actual auth
  mechanism.
- No HSTS header is set. Deliberate: Cadence does not terminate TLS itself (documented in the
  README as a reverse-proxy responsibility), and setting HSTS from behind a proxy that may not
  always be HTTPS-only in a given operator's setup risks a self-inflicted lockout; left to the
  reverse-proxy layer to add if desired.
- Alembic is installed as a dependency but no migration has been authored; schema changes today
  happen via SQLAlchemy's `create_all()`, which only creates missing tables and never alters
  existing ones. Fine for a fresh v0.1.0 install; a real migration is needed before any
  backward-incompatible schema change ships in a later version (documented as the first "next
  thing worth doing" below).

### Test coverage summary

- **Backend**: 202 tests (unit + integration), 87% line coverage, CI gate set at 80%. Unit tests
  cover search-result normalization per source (including malformed JSON, timeouts, HTTP errors,
  empty results for all four sources), candidate scoring and tie-breaking, path-traversal
  defenses against a battery of real attack strings, password hashing and session-token
  lifecycle (including tampered and expired tokens), request idempotency (including
  rejected/failed re-submission and cross-user isolation), the SSRF policy engine directly
  (loopback/private/metadata/multicast addresses under both policies), and the new MBID
  validation. Integration tests exercise every route against a real (temp-file) SQLite
  database, and the fulfillment worker's Prowlarr-first/Soulseek-fallback logic against mocked
  Prowlarr/qBittorrent/slskd HTTP responses, including the full success path through both
  backends and worker crash-recovery.
- **Web client**: 19 Vitest/Testing-Library component and unit tests (status badge rendering
  per status, track card rendering and request-button state machine, API client error handling
  and auth-header attachment).
- **End-to-end**: 8 Playwright tests running against the real backend in demo mode serving the
  real built web client (the actual production topology, not a dev-server proxy or a mocked
  API): search returns results across every one of the four tabs, a user can submit a request
  and see it pending, an admin can see and approve a pending request, request history displays
  every seeded status (pending/approved/downloading/fulfilled/rejected/failed), a fulfilled
  request's detail view shows its filed path, a failed request's detail view shows its failure
  reason, and unauthenticated/non-admin access is correctly redirected. All 8 pass.

### Dependency list with justifications

Backend (`backend/pyproject.toml`):

| Package                                                                                | Why                                                                                       |
| -------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| fastapi, uvicorn[standard]                                                             | Mission-specified web framework and ASGI server                                           |
| pydantic, pydantic-settings, email-validator                                           | Request/response models and typed env-var config                                          |
| sqlalchemy, aiosqlite, alembic                                                         | Async ORM, SQLite driver, migration tooling (installed, not yet used - see gaps)          |
| httpx                                                                                  | Async HTTP client, wrapped by the custom SSRF-safe client                                 |
| passlib[argon2], argon2-cffi                                                           | Password hashing                                                                          |
| pyjwt                                                                                  | Session token signing/verification                                                        |
| python-multipart                                                                       | Required by FastAPI for form data (future-proofing; not currently exercised by any route) |
| mutagen                                                                                | Audio file tag reading/writing (MP3/FLAC)                                                 |
| slowapi                                                                                | Rate limiting                                                                             |
| tenacity                                                                               | Bounded retry policy for outbound HTTP calls                                              |
| structlog                                                                              | Structured JSON logging                                                                   |
| pytest, pytest-asyncio, pytest-cov, pytest-httpx, ruff, mypy, pip-audit, types-passlib | Dev/test/lint tooling only, not shipped in the runtime image                              |

Web (`web/package.json`):

| Package                                                                                       | Why                                                                            |
| --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| react, react-dom                                                                              | UI framework                                                                   |
| react-router-dom                                                                              | Client-side routing with auth-guarded routes                                   |
| @tanstack/react-query                                                                         | Server-state caching and the polling behavior request history needs            |
| vite, @vitejs/plugin-react, typescript                                                        | Build tooling                                                                  |
| eslint + plugins, prettier                                                                    | Lint/format, matching the repo's stated Python-equivalent tooling (ruff/black) |
| vitest, @testing-library/react, @testing-library/jest-dom, @testing-library/user-event, jsdom | Component/unit testing                                                         |
| @playwright/test                                                                              | End-to-end testing                                                             |

A full generated `docs/PACKAGES.md` (gitignored, regenerate locally via the `packages` skill)
was not generated as part of this session; noted as a next-step below.

### Deployment steps

1. `cp .env.example .env`, fill in at least `ADMIN_BOOTSTRAP_EMAIL`/`ADMIN_BOOTSTRAP_PASSWORD`
   (or leave `DEMO_MODE=true` to explore without any configuration).
2. `docker compose up -d`. This builds the multi-stage image (Node stage builds the web client,
   Python stage installs the backend into a venv, final stage is a non-root user running
   uvicorn on `:8000`), and starts the container with a named volume for `/app/data` (SQLite +
   staging area) and `/app/library` (fulfilled tracks).
3. Point the operator's existing reverse proxy (Nginx Proxy Manager, in his case) at `:8000`
   for TLS termination and a real hostname. No CORS configuration is needed since one container
   serves both the API and the web client from the same origin.
4. To wire Prowlarr/qBittorrent/slskd: uncomment and fill in the relevant block in
   `docker-compose.yml`'s `environment:` section, using the operator's own Docker network
   service names (a commented `networks:` block is provided for that case too).
5. `docker compose up -d` again to pick up the new environment; `/api/health` will report
   `configured: true` (and `reachable` once the integration responds) for whichever
   integrations were just added, without needing zero-downtime handling since Cadence
   feature-detects at request time, not at boot.

### Known gaps, ranked by risk

1. **No Alembic migration authored yet** (medium risk once a schema-changing v0.2.0 ships):
   `create_all()` only adds missing tables, never alters existing ones. Any future column
   change needs a real migration or existing self-hosted installs will break silently on
   upgrade.
2. **Spotify personalization tab (opt-in, per-user Authorization Code OAuth) not built** (low
   risk, explicitly additive per the mission): the mission describes this as "a later, explicitly
   optional" tab. The app-only Client Credentials search tab it augments is fully built; the
   personalization tab was deprioritized to keep this phase's scope to what the mission marks
   as required now versus later.
3. **No load/stress testing of the fulfillment worker under many concurrent approved requests**
   (low risk for the stated single-household deployment target): the worker processes one job
   at a time by design, so this is a throughput characteristic, not a correctness risk, but has
   not been measured.
4. **docs/PACKAGES.md not generated** (low risk, purely a documentation completeness gap):
   the repo lifecycle standard calls for this; not done in this session, easy to add via the
   `packages` skill in a follow-up pass.

### Next three things worth doing

1. Author a real Alembic migration (baseline + a no-op first revision) so the upgrade path from
   this v0.1.0 schema is safe once any future field is added or changed.
2. Build the opt-in Spotify personalization tab (per-user Authorization Code OAuth, saved
   tracks/playlists as a quick-pick source), the one explicitly-deferred piece of the Phase 1
   spec.
3. Generate `docs/PACKAGES.md` and run the `security-baseline` skill's fuller checklist pass
   (this report's security section was done by hand against the mission's specific callouts,
   not the skill's full generic checklist) before the first tagged release.

### Decisions made without asking (per the mission's "make the most sensible call" instruction)

- React over SvelteKit, in-process asyncio worker over `arq`, argon2 over bcrypt, slowapi for
  rate limiting: all justified above in the stack-justification section.
- Demo admin/user account emails use a `.internal` TLD rather than `.local`, because
  `email-validator` (which Pydantic's `EmailStr` uses) unconditionally rejects `.local` as an
  mDNS-reserved special-use domain regardless of deliverability-check settings; discovered
  while first wiring up demo-mode auth tests.
- The library file-move step re-searches and re-scores from scratch on worker restart rather
  than attempting to resume an in-flight download exactly where it left off; simpler, and safe
  given how cheap both Prowlarr grabs and Soulseek downloads are to re-initiate, at the cost of
  redoing already-completed download progress on the rare crash-mid-download case.
- Rate limiting the request-creation route (`POST /api/requests`) at 20/min per IP, which the
  mission does not explicitly call out (only "search and auth" are named) but is a reasonable
  extension since request creation is the trigger for real downstream work once approved.

### Anything attempted and abandoned

Nothing was fully abandoned mid-implementation in this phase. The one meaningful scope
narrowing was the Spotify personalization tab, addressed above as a known gap and a next step
rather than an attempted-and-abandoned effort, since it was never started.
