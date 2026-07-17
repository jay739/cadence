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

## Addendum: Admin-editable integration settings (2026-07-17)

This section covers the addition specified in `MISSION_settings_ui.md`: a database-backed,
admin-editable settings store for the Prowlarr/qBittorrent/slskd/Spotify/library fields that
previously only loaded from environment variables at boot. Phase 1's section above is
unchanged and still describes the shipped baseline this addendum builds on.

### What was built and why this shape

**Database model**: a single `IntegrationSetting` table
(`backend/app/models/integration_setting.py`), one row per `(integration, field_name)` pair,
rather than one wide table with a fixed column per field. Chosen because: adding a field to an
integration later (or a new integration entirely) becomes a data change, not a schema
migration; masking/encryption is naturally a per-field concern here (`is_secret` is a column,
not a naming convention spread across many columns, since some fields on the same integration
are secrets and others aren't, e.g. `qbittorrent_url` vs `qbittorrent_password`); and a partial
update from the admin UI (edit just one field) maps directly onto upserting a single row
instead of a read-modify-write of a wide row. The trade-off, accepted deliberately: every
stored value is a string (no per-column typing), which costs nothing here since every field in
scope is already a string in `Settings`.

**Encryption**: `backend/app/core/crypto.py` wraps Fernet (AES-128-CBC + HMAC-SHA256,
authenticated, from the `cryptography` package, added as a new backend dependency) for
encrypt/decrypt, plus a `mask_secret()` helper for API responses. The key is managed by a new
`Settings.resolved_settings_encryption_key()` in `config.py`, extending the exact
generate-and-persist pattern `resolved_session_secret()` already used (lazy-generate on first
boot, written to a file under `DATA_DIR` with owner-only `0600` permissions), per the mission's
explicit instruction not to invent a second secret-management mechanism. It is a **separate
file** (`settings_encryption.key`, sibling to `session_secret.key`), not a key derived from
`SESSION_SECRET`, because the two keys protect different things with different rotation
implications: `SESSION_SECRET` signs JWTs, and rotating it just invalidates active sessions
(cheap, users log in again); this key decrypts persisted integration credentials, and rotating
it without a re-encryption pass would permanently strand every already-saved secret in the
database. Keeping them independent means a future session-secret rotation can never
accidentally brick saved Prowlarr/qBittorrent/slskd/Spotify credentials, and vice versa.

**Precedence rule chosen: an environment variable, when set, always wins over a DB-stored
value for the same field.** DB-stored values are the fallback/normal runtime path used
whenever the env var is unset. Implemented in `backend/app/services/settings_store.py`
(`get_effective_settings()`, `apply_effective_settings()`), with a `FIELD_REGISTRY` naming
exactly the eleven fields in scope (`prowlarr_url`, `prowlarr_api_key`, `qbittorrent_url`,
`qbittorrent_username`, `qbittorrent_password`, `slskd_url`, `slskd_api_key`,
`spotify_client_id`, `spotify_client_secret`, `music_library_path`, `library_path_pattern`).
Chosen over "DB overrides env once present" because: it matches the mission's framing of env
vars as the bootstrap/override path (an operator who sets one is making a deliberate
infrastructure decision that should not be silently shadowed by whatever was last clicked in a
web form); it keeps every existing docker-compose-only deployment byte-for-byte unaffected,
since nothing in the UI could ever race ahead of an env var that's already set, making this
addition strictly additive; and it gives an operator a clean escape hatch if a UI-saved value
is ever wrong or the encryption key is lost (unset the DB row, or just ignore it, and set the
env var, which takes over immediately on the next request with no manual DB surgery). On boot,
`seed_db_from_env_on_boot()` copies any currently-set env var into its DB row (only when the
env var is actually set; it never clears a DB row just because an env var happens to be absent
that boot), so the settings page can show "sourced from the environment" with the actual value
rather than a blank field, and so a value survives if an operator later removes the env var to
switch to UI-managed configuration.

**Live refresh, replacing `get_settings()`'s cache for these fields only**:
`apply_effective_settings()` takes the process's cached `Settings` object and returns a copy
with only the registry fields overlaid from the fresh env-or-DB resolution, done via
`model_copy(update=...)` and _only_ for fields where the resolver actually found a value.
This is purely additive: a field with neither an env var nor a DB row keeps whatever the
passed-in `Settings` object already carried (important for tests and any future non-HTTP
caller that constructs `Settings` directly). Wired in at the three places that read these
fields today, at their existing `session`-bearing call sites, with no signature break for
existing callers: `app.services.search.multi_source_search` (session is now an optional
parameter, defaulting to `None`, so the handful of unit tests that call it with a bare
`Settings` and no DB still pass unchanged), `app.services.fulfillment.fulfill_request` (already
received `session`, so the overlay is applied at the top of the function body, meaning even a
long-running `FulfillmentWorker` picks up a mid-session settings save on its very next
fulfillment pass), and `GET /api/health` (applied before computing `configured`/`reachable`
booleans, skipped only in demo mode). No new process-wide cache was introduced; every call
re-queries the (small, indexed) `integration_settings` table, which is the deliberate trade a
mission explicitly asked for over `get_settings()`'s `lru_cache` for exactly these fields.

### New routes, with validation and auth

All under `backend/app/api/routes_settings.py`, prefix `/api/admin/settings`, guarded by the
same `require_admin` dependency the existing approval-queue admin routes use (403 for a
non-admin, 401 unauthenticated).

| Method & path                                                          | Auth       | Validation                                                                                                                                              |
| ---------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/admin/settings`                                              | admin only | n/a; every secret field masked (`mask_secret`, e.g. `••••••••ab12`), plus a `source` tag (`env`/`db`/`unset`) per field                                 |
| `PUT /api/admin/settings/{prowlarr,qbittorrent,slskd,spotify,library}` | admin only | one Pydantic model per integration (`ProwlarrSettingsIn`, etc.), every field optional (omit = leave unchanged), length-capped                           |
| `POST /api/admin/settings/{integration}/test`                          | admin only | 404 for an unknown integration name; makes exactly one outbound call via the existing `SafeHttpClient`, never persists anything, reports `{ok, detail}` |

No new rate limit was added to these routes: they are admin-only (already the narrowest
practical audience) and, unlike public auth/search endpoints, are not exposed to
unauthenticated or general-user traffic, so a credential-stuffing/abuse rate limit does not
apply the same way; this mirrors the existing approval-queue admin routes, which are likewise
unrated-limited beyond their auth gate.

### Test coverage added

- **Unit** (`backend/tests/unit/`): `test_crypto.py` (encrypt/decrypt round trip, ciphertext
  never contains the plaintext, wrong-key and tampered-ciphertext both raise a typed
  `DecryptionError`, repeated encryption of the same value is non-deterministic, `mask_secret`
  never leaks the real value); `test_settings_precedence.py` (field registry completeness and
  scope, DB-only resolution, env-overrides-DB for both plain and secret fields,
  `apply_effective_settings` is additive and never wipes a constructor-supplied value, the
  core "no restart" guarantee proven directly by calling `apply_effective_settings` twice with
  an `upsert_setting` in between, boot-time env-to-DB seeding); `test_settings_test_connection.py`
  (all four per-integration checks, success/auth-failure/server-error/missing-fields, and an
  unreachable-host case proving `SafeHttpError` from the DNS-check layer is turned into a
  reported failure, never a raw exception).
- **Integration** (`backend/tests/integration/`): `test_settings_routes.py` (non-admin
  rejection on every route including test-connection, a saved secret's GET response is masked
  and never contains the plaintext anywhere in the response body, partial updates leave
  untouched fields alone, an env var visibly overrides a saved DB value in the GET response,
  test-connection never persists regardless of outcome, unknown-integration 404s, oversized
  input 422s); a new class in `test_fulfillment.py` proving the worker's actual read path
  (`fulfill_request`, not just `/api/health`/`/api/search`) uses a Prowlarr connection that
  exists _only_ as a DB row (no env var, no constructor override); `test_settings_takes_effect_live.py`,
  the mission's required end-to-end proof, hits the real app instance through its HTTP routes
  exactly as an admin's browser would: read `/api/health` (not configured), `PUT` a Prowlarr/
  slskd connection through the settings API, read `/api/health` again in the same test (same
  running app, same DB engine, no fixture re-entry, no cache clear) and confirm `configured`
  flips to `true`; a further case in the same file proves the live `reachable` probe and the
  `/api/search` fan-out both honor the DB-saved value too, not just the feature-detection
  boolean.
- **Web** (`web/src/`): `AdminSettingsPage.test.tsx` (all five integrations render grouped, a
  masked secret is never pre-filled into its input's value, an env-sourced field renders
  disabled with an explanatory note, the Save button stays disabled until a field is actually
  edited and submits only the changed fields, Test connection surfaces its result message,
  Library correctly has no Test-connection button); three new assertions added to
  `client.test.ts` for the new `api.adminSettings.get/update/testConnection` methods.
- **End-to-end** (`web/e2e/demo-flow.spec.ts`, Playwright against the real backend in demo
  mode serving the real built client): a non-admin is redirected away from
  `/admin/settings`; an admin opens the Settings page, sees all five integration sections,
  edits and saves a Prowlarr field, and sees it reflected back tagged as saved (`db` source),
  proving the real round trip through the browser rather than an optimistic local echo; a
  Test-connection click against an unreachable demo host renders a result message rather than
  hanging, timing out, or crashing the page.
- **Full existing suite re-run**: backend 264 tests passing (was 202 before this addendum),
  88% coverage (CI gate is 80%), ruff and mypy both clean; web 28 Vitest tests passing (was
  19), lint and typecheck both clean; all 11 Playwright e2e tests passing (was 8); production
  web build (`npm run build`) still succeeds. Nothing from Phase 1's suite was touched or
  weakened to make this pass; the two places an existing test's assumption needed adjusting
  were both cases where the test's `Settings(...)` fixture value or the test harness's own
  `MUSIC_LIBRARY_PATH` env var was, correctly, no longer being silently bypassed by the new
  precedence logic (documented inline in those tests).

### Security checklist for this addition

- Secrets are never returned unmasked once saved, in any route, including the update routes
  (which echo back the fresh masked view, not the submitted plaintext).
- Test-connection uses the existing `SafeHttpClient` exclusively (`POLICY_ALLOW_PRIVATE` for
  Prowlarr/qBittorrent/slskd, matching how the live search/fulfillment code treats them;
  `POLICY_PUBLIC_ONLY` for Spotify's token endpoint), so it inherits the same SSRF protections
  (DNS resolved and re-checked before every attempt, link-local/metadata/loopback blocked) as
  every other outbound call in the codebase. No second HTTP path was added.
- Masking is verified at the full HTTP response level, not just the parsed field: integration
  tests assert the raw response text (`resp.text`) never contains a saved secret's plaintext
  anywhere, not only that the parsed `value` field is masked.
- Every settings route requires `require_admin`; verified by tests for both an unauthenticated
  caller (401) and an authenticated non-admin (403), on every route including test-connection.
- Encryption key file (`settings_encryption.key`) follows the same 0600, `DATA_DIR`-relative,
  gitignored-by-directory pattern as `session_secret.key`; never logged, never part of any API
  response.

### Known gaps

1. **No rotation tooling for `settings_encryption.key`** (low-to-medium risk, same class of gap
   Phase 1 already accepted for `SESSION_SECRET`/Alembic): if the key file is lost or
   deliberately rotated, every previously saved secret becomes an unrecoverable
   `DecryptionError` on read; `_load_db_values` degrades this to "value unavailable" (returns
   `None` for that field) rather than crashing the settings page, but there is no admin-facing
   "your saved Prowlarr key could not be decrypted, please re-enter it" messaging yet, just a
   silently-empty field. Acceptable for a first pass; worth a follow-up if key rotation ever
   becomes a real operational need.
2. **Test-connection routes are not rate-limited.** They are admin-only, which is the main
   mitigation, but an admin session with a compromised token could still be used to hammer an
   internal Prowlarr/qBittorrent/slskd instance via repeated test-connection calls. Low risk in
   a single-admin, self-hosted context, but noted honestly rather than silently.
3. **`docs/PACKAGES.md`** still not generated (a pre-existing Phase 1 gap, not introduced by
   this addendum, but the new `cryptography` dependency should be included whenever it is).
4. **Web `types/api.ts` has one pre-existing line (`RequestStatus`, unrelated to this
   addendum) that a newer Prettier version now reports as unformatted** (`format:check` warns
   on it). This was already present, byte-for-byte, in the Phase 1 commit, so it was left
   untouched rather than folded into this addition's diff; worth a dedicated formatting-only
   commit later rather than fixing incidentally here.

### Decisions made without asking (per the mission's "make the most sensible call" instruction)

- One `IntegrationSetting` row per `(integration, field_name)` rather than one row/table per
  integration: justified in "What was built" above.
- A dedicated encryption key file, sibling to (not derived from) `SESSION_SECRET`: justified
  above; the mission left this "your call" but was explicit that it must extend, not replace or
  duplicate, the existing generate-and-persist mechanism, which this does structurally while
  keeping the two keys' blast radii independent.
- Env-always-wins precedence (vs. "DB overrides env once present"): justified above.
- `music_library_path`/`library_path_pattern` included in the DB-backed registry alongside the
  four integrations, per the mission's explicit item 1 listing; no additional non-credential
  fields (e.g. fulfillment attempt budgets) were added to the registry, per the mission's
  explicit scoping instruction.
- No additional rate limit on the new admin routes, matching the existing approval-queue admin
  routes' precedent (admin-only auth is the gate, not a request-rate budget).
