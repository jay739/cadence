# Mission addendum: Admin-editable integration settings

This is an addition to the already-shipped Phase 1 (see MISSION.md and REPORT.md's "Phase 1"
section, which is complete and must not be reverted or re-litigated). Read both before starting.
This addendum has its own REPORT.md section requirement at the end; append it, do not touch the
existing Phase 1 section.

## Why this exists

Phase 1 only reads Prowlarr/qBittorrent/slskd/Spotify/library connection details from
environment variables at boot, via `backend/app/core/config.py`'s `Settings` object (loaded
once, cached by `get_settings()`). That means every operator who wants to actually run Cadence
has to SSH into their host and hand-edit a `.env` file to wire up their own instances, then
restart the container. That is a real usability gap for a tool meant to be self-hosted by
people other than its author: it should be configurable from the admin UI, the same way an
admin approves requests from the UI rather than editing the database directly.

## What to build

1. **A database-backed settings store.** Add an `IntegrationSettings`-style table (or one row
   per integration, your call, justify the shape in the addendum REPORT.md section) holding the
   same fields currently in `Settings` for Prowlarr, qBittorrent, slskd, Spotify, and the
   library path/pattern. Secrets (API keys, qBittorrent password, Spotify client secret) must be
   encrypted at rest, not stored in plaintext in the SQLite file. Use a symmetric encryption key
   derived from or stored alongside `SESSION_SECRET`'s existing generate-and-persist pattern in
   `config.py` (do not invent a second secret-management mechanism; extend the existing one).
2. **Precedence: environment variables remain the bootstrap/override path, DB-stored values are
   the normal runtime path.** On boot, if an env var for a given integration field is set, it
   seeds (or overrides, your call, document which) the DB-stored value so existing
   docker-compose-based deployments (env-var only, no UI interaction) keep working unchanged.
   Once a value exists in the DB and no env var overrides it, admin UI edits take effect
   immediately without a restart. `get_settings()`'s cached, process-wide object is the wrong
   shape for values that can change at runtime from the UI; refactor the read path for these
   specific fields so a save from the settings UI is reflected on the next request, not only
   after a restart. Fields that are not integration credentials (e.g. `SESSION_TTL_MINUTES`,
   `OUTBOUND_TIMEOUT_SECONDS`) can stay env-only; scope this addition to exactly the fields
   listed in item 1.
3. **Backend API**: admin-only routes to read the current settings (with secrets masked in the
   response, e.g. show `PROWLARR_API_KEY` as `"••••••••ab12"` or similar, never the real value
   once saved) and to update them (accepting a full or partial payload per integration). A
   "test connection" action per integration (Prowlarr, qBittorrent, slskd, Spotify) that makes
   one real outbound call using the SafeHttpClient and reports success/failure without
   persisting anything, so an admin can validate credentials before saving.
4. **Admin settings page in the web client**: one page, grouped by integration (Prowlarr,
   qBittorrent, slskd, Spotify, Library), each with its fields, a masked display of any
   already-saved secret, a "Test connection" button wired to the backend action above, and a
   save action. Reachable only to the admin role, same guard pattern as the existing admin
   approval queue page.
5. **`/api/health`'s existing configured/reachable booleans must reflect DB-stored settings too**,
   not just env vars, since that endpoint is how the rest of the app (and any future native
   client) knows what is actually usable.

## Constraints carried over from MISSION.md (still apply, do not relax them)

- No secret ever committed to the repo, logged, or returned unmasked once saved.
- All outbound calls, including the new "test connection" actions, go through the existing
  SSRF-hardened `SafeHttpClient`. Do not add a second, unguarded HTTP path for this feature.
- Path safety, rate limiting, CSP, and the existing auth/session model all still apply
  unchanged to any new route.
- Small, focused, conventionally named commits. Author every commit as
  "Jayakrishna Konda <contact@jay739.dev>". No AI attribution anywhere. Do not push.
- Full tests for everything new: unit tests for the encryption/decryption round trip and the
  env-var-vs-DB precedence logic, integration tests for the new routes (including a masked-value
  response and a rejected non-admin request), an end-to-end test that saves a setting through
  the UI and confirms it takes effect (e.g. `/api/health` flips a `configured` boolean) without
  a restart. Update the existing Playwright demo-mode suite if the new page needs a demo-mode
  fixture.
- Re-run the full existing test suite (backend and web) and confirm it is still green; this
  addition must not regress anything from the Phase 1 REPORT.md.

## Definition of done

- An admin can open the settings page, enter real Prowlarr/qBittorrent/slskd/Spotify details,
  test each connection, save, and have search/fulfillment immediately use those values, with
  zero SSH access or file editing required.
- `docker-compose.yml` and `.env.example` still work exactly as before for an operator who
  prefers env-var-only configuration (this is additive, not a breaking change to the existing
  deployment path).
- Append a new dated section to REPORT.md (do not touch the existing Phase 1 section) covering:
  what was built, the encryption approach and why, the precedence rule chosen and why, the new
  routes with their validation/auth, test coverage added, and any gaps.
