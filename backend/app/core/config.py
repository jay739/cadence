"""Runtime configuration, sourced entirely from environment variables.

Nothing in this module or anywhere else in the repo may hardcode a real URL, token, indexer
name, or credential. Every integration is optional at boot and feature-detected: Cadence must
start and serve search/browsing even when no fulfillment backend is configured.
"""

from __future__ import annotations

import contextlib
import secrets
import stat
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Core ---
    app_name: str = "Cadence"
    version: str = "0.1.0"
    demo_mode: bool = Field(default=False, alias="DEMO_MODE")
    data_dir: str = Field(default="./data", alias="DATA_DIR")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")

    # --- Auth / sessions ---
    session_secret: str | None = Field(default=None, alias="SESSION_SECRET")
    admin_bootstrap_email: str | None = Field(
        default=None, alias="ADMIN_BOOTSTRAP_EMAIL"
    )
    admin_bootstrap_password: str | None = Field(
        default=None, alias="ADMIN_BOOTSTRAP_PASSWORD"
    )
    session_ttl_minutes: int = Field(default=60 * 24 * 7, alias="SESSION_TTL_MINUTES")
    # Secure-by-default: the session cookie carries auth for the OAuth redirect flow and the
    # streaming search (EventSource cannot send an Authorization header), and browsers only
    # transmit Secure cookies over HTTPS or on localhost. Operators serving plain HTTP on a
    # LAN/Tailscale IP (no reverse proxy yet) must set COOKIE_SECURE=false or those two flows
    # fail with "Not authenticated" while everything Bearer-token-based still works.
    cookie_secure: bool = Field(default=True, alias="COOKIE_SECURE")

    # --- Prowlarr ---
    prowlarr_url: str | None = Field(default=None, alias="PROWLARR_URL")
    prowlarr_api_key: str | None = Field(default=None, alias="PROWLARR_API_KEY")

    # --- qBittorrent ---
    qbittorrent_url: str | None = Field(default=None, alias="QBITTORRENT_URL")
    qbittorrent_username: str | None = Field(default=None, alias="QBITTORRENT_USERNAME")
    qbittorrent_password: str | None = Field(default=None, alias="QBITTORRENT_PASSWORD")

    # --- slskd (Soulseek) ---
    slskd_url: str | None = Field(default=None, alias="SLSKD_URL")
    slskd_api_key: str | None = Field(default=None, alias="SLSKD_API_KEY")

    # --- ytdlp-shim (last-resort YouTube audio fallback, own isolated process) ---
    ytdlp_shim_url: str | None = Field(default=None, alias="YTDLP_SHIM_URL")

    # Navidrome as an authentication backend, so library users sign in with the credentials
    # they already have. Unset means local accounts only, which stays the default.
    navidrome_url: str | None = Field(default=None, alias="NAVIDROME_URL")

    # An optional Navidrome service account, used only to ask whether a track is already in the
    # library before someone requests it (app.services.navidrome_library). This cannot reuse the
    # requesting user's own credential: Cadence never stores a Navidrome password, so there is
    # nothing to replay outside a login request. Leaving these unset disables the
    # already-in-library check and nothing else -- search, login and fulfillment are unaffected.
    # An ordinary (non-admin) Navidrome account is sufficient; the check only ever reads.
    navidrome_admin_user: str | None = Field(default=None, alias="NAVIDROME_ADMIN_USER")
    navidrome_admin_password: str | None = Field(
        default=None, alias="NAVIDROME_ADMIN_PASSWORD"
    )

    # --- Spotify (Client Credentials, app-only) ---
    spotify_client_id: str | None = Field(default=None, alias="SPOTIFY_CLIENT_ID")
    spotify_client_secret: str | None = Field(
        default=None, alias="SPOTIFY_CLIENT_SECRET"
    )

    # --- Library ---
    music_library_path: str = Field(
        default="./data/library", alias="MUSIC_LIBRARY_PATH"
    )
    library_path_pattern: str = Field(
        default="{artist}/{title}.{ext}", alias="LIBRARY_PATH_PATTERN"
    )

    # --- Fulfillment tuning ---
    fulfillment_prowlarr_attempt_budget: int = Field(
        default=5, alias="FULFILLMENT_PROWLARR_ATTEMPT_BUDGET"
    )
    fulfillment_poll_interval_seconds: int = Field(
        default=10, alias="FULFILLMENT_POLL_INTERVAL_SECONDS"
    )
    fulfillment_download_timeout_seconds: int = Field(
        default=900, alias="FULFILLMENT_DOWNLOAD_TIMEOUT_SECONDS"
    )
    # A candidate can score well and still fail for a reason scoring cannot see (a torrent
    # stuck at zero peers, a Soulseek user gone offline, a YouTube video pulled between being
    # indexed and being fetched). Each tier falls through to the next-best qualifying
    # candidate, up to this many tries, before giving up on that tier entirely.
    fulfillment_max_candidate_attempts: int = Field(
        default=3, alias="FULFILLMENT_MAX_CANDIDATE_ATTEMPTS"
    )

    # --- Settings source override (break-glass, issue #18) ---
    # An admin can flip individual integrations to "database wins" in the settings UI (see
    # app.services.settings_store). This is the recovery path if a bad DB-stored value ever
    # locks Cadence out of an integration it needs: setting this env var forces every
    # integration back to "env wins" regardless of what's stored, exactly like the behavior
    # before per-integration preferences existed, without having to fix or delete anything in
    # the database first.
    settings_force_env_precedence: bool = Field(
        default=False, alias="SETTINGS_FORCE_ENV_PRECEDENCE"
    )

    # Keyed by client IP, which is not always one person: behind a reverse proxy that doesn't
    # forward the real address, or a shared/CGNAT egress, a whole household shares one bucket.
    # See app.core.limiter.auth_rate_limit.
    auth_rate_limit: str = Field(default="10/minute", alias="AUTH_RATE_LIMIT")

    # A floor on free space in the music library, checked before a download starts. A full
    # pool otherwise fails late and messily: bandwidth and indexer budget already spent, and
    # the error surfacing as a write failure at filing time. 0 disables the check.
    min_free_disk_gb: float = Field(default=5.0, alias="MIN_FREE_DISK_GB")

    # --- Outbound HTTP behavior ---
    outbound_timeout_seconds: float = Field(
        default=8.0, alias="OUTBOUND_TIMEOUT_SECONDS"
    )
    outbound_max_retries: int = Field(default=2, alias="OUTBOUND_MAX_RETRIES")

    # --- CORS / web origin (same-origin by default; only set if serving cross-origin) ---
    cors_allow_origins: str = Field(default="", alias="CORS_ALLOW_ORIGINS")

    # --- Public base URL, needed to build the Spotify OAuth redirect_uri ---
    app_base_url: str | None = Field(default=None, alias="APP_BASE_URL")

    @field_validator("music_library_path", "data_dir")
    @classmethod
    def _resolve_path(cls, v: str) -> str:
        return str(Path(v).expanduser())

    @property
    def data_dir_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def music_library_path_resolved(self) -> Path:
        """The canonical, resolved absolute root of the music library.

        All file-move/tag-writing operations must confirm their destination path resolves to
        a location inside this root (see app.services.pathsafety) before touching disk.
        """
        p = Path(self.music_library_path)
        p.mkdir(parents=True, exist_ok=True)
        return p.resolve()

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        db_path = self.data_dir_path / "cadence.db"
        return f"sqlite+aiosqlite:///{db_path}"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    def resolved_session_secret(self) -> str:
        """Return the session secret, generating and persisting one on first boot if unset.

        Written with owner-only (0600) permissions, as required by the mission.
        """
        if self.session_secret:
            return self.session_secret
        secret_path = self.data_dir_path / "session_secret.key"
        if secret_path.exists():
            return secret_path.read_text(encoding="utf-8").strip()
        secret = secrets.token_urlsafe(64)
        secret_path.write_text(secret, encoding="utf-8")
        # Best-effort: some filesystems (e.g. certain FUSE mounts) don't support chmod.
        with contextlib.suppress(OSError):
            secret_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        self.session_secret = secret
        return secret

    def resolved_settings_encryption_key(self) -> bytes:
        """Return the Fernet key used to encrypt DB-stored integration secrets at rest.

        Extends the exact generate-and-persist pattern used by `resolved_session_secret()`
        above (same directory, same lazy-generate-on-first-boot behavior, same owner-only 0600
        permissions) rather than inventing a second secret-management mechanism, per the
        mission's explicit instruction. This is a *separate file* from session_secret.key, not
        a key derived from SESSION_SECRET, because the two keys protect different things with
        different rotation implications: SESSION_SECRET signs JWTs (rotating it invalidates
        every logged-in session, which is fine, sessions are cheap to reissue) while this key
        decrypts persisted integration credentials (rotating it without a re-encryption pass
        would permanently strand every already-saved secret in the database). Keeping them
        independent means a future session-secret rotation can never accidentally brick saved
        Prowlarr/qBittorrent/slskd/Spotify credentials, and vice versa.
        """
        key_path = self.data_dir_path / "settings_encryption.key"
        if key_path.exists():
            return key_path.read_text(encoding="utf-8").strip().encode("utf-8")
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        key_path.write_text(key.decode("utf-8"), encoding="utf-8")
        with contextlib.suppress(OSError):
            key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return key

    # --- Feature detection helpers, used by /api/health and the fulfillment worker ---
    @property
    def prowlarr_configured(self) -> bool:
        return bool(self.prowlarr_url and self.prowlarr_api_key)

    @property
    def qbittorrent_configured(self) -> bool:
        return bool(
            self.qbittorrent_url
            and self.qbittorrent_username
            and self.qbittorrent_password
        )

    @property
    def slskd_configured(self) -> bool:
        return bool(self.slskd_url and self.slskd_api_key)

    @property
    def ytdlp_shim_configured(self) -> bool:
        return bool(self.ytdlp_shim_url)

    @property
    def spotify_configured(self) -> bool:
        return bool(self.spotify_client_id and self.spotify_client_secret)

    @property
    def navidrome_library_configured(self) -> bool:
        """Whether the already-in-library check can run at all.

        Separate from `navidrome_url` being set: Navidrome can be a perfectly good login backend
        with no service account configured, in which case sign-in works and library badges
        simply never appear.
        """
        return bool(
            self.navidrome_url
            and self.navidrome_admin_user
            and self.navidrome_admin_password
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
