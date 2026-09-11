"""Pydantic models for the admin-only integration settings API.

Covers exactly the fields the mission addendum scopes this feature to: Prowlarr, qBittorrent,
slskd, Spotify, and the library path/pattern. Never includes non-credential tuning fields
(SESSION_TTL_MINUTES, OUTBOUND_TIMEOUT_SECONDS, fulfillment budgets), which stay env-only.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FieldValue(BaseModel):
    """One field's current effective value plus where it came from.

    `value` is masked for secret fields (see app.core.crypto.mask_secret); never the real
    plaintext once a secret has been saved. `source` is "env" (an environment variable is set
    and wins per the precedence rule), "db" (no env var, a database-stored value is in effect),
    or "unset" (neither).
    """

    value: str | None
    source: str = Field(description='"env" | "db" | "unset"')
    is_secret: bool


class ProwlarrSettingsOut(BaseModel):
    prowlarr_url: FieldValue
    prowlarr_api_key: FieldValue


class QBittorrentSettingsOut(BaseModel):
    qbittorrent_url: FieldValue
    qbittorrent_username: FieldValue
    qbittorrent_password: FieldValue


class SlskdSettingsOut(BaseModel):
    slskd_url: FieldValue
    slskd_api_key: FieldValue


class SpotifySettingsOut(BaseModel):
    spotify_client_id: FieldValue
    spotify_client_secret: FieldValue


class LibrarySettingsOut(BaseModel):
    music_library_path: FieldValue
    library_path_pattern: FieldValue


class SourcePreferenceOut(BaseModel):
    """Which source this integration is set to prefer, and whether that choice is currently
    overridden by the break-glass env var (see Settings.settings_force_env_precedence).
    """

    preference: str = Field(description='"env" | "database"')
    forced_env: bool = Field(
        description="True when SETTINGS_FORCE_ENV_PRECEDENCE is overriding `preference`"
    )


class SourcePreferenceIn(BaseModel):
    preference: str = Field(description='"env" | "database"')


class IntegrationSettingsOut(BaseModel):
    prowlarr: ProwlarrSettingsOut
    qbittorrent: QBittorrentSettingsOut
    slskd: SlskdSettingsOut
    spotify: SpotifySettingsOut
    library: LibrarySettingsOut
    source_preferences: dict[str, SourcePreferenceOut]


# --- Update payloads: partial per-integration, per the mission's "full or partial payload". ---
# A field omitted (None) is left unchanged; there is no way to explicitly clear a field to
# empty via these payloads other than sending an empty string, which is accepted (a blank
# string is treated as "no value" by the feature-detection `*_configured` properties anyway).


class ProwlarrSettingsIn(BaseModel):
    prowlarr_url: str | None = Field(default=None, max_length=2048)
    prowlarr_api_key: str | None = Field(default=None, max_length=512)


class QBittorrentSettingsIn(BaseModel):
    qbittorrent_url: str | None = Field(default=None, max_length=2048)
    qbittorrent_username: str | None = Field(default=None, max_length=255)
    qbittorrent_password: str | None = Field(default=None, max_length=512)


class SlskdSettingsIn(BaseModel):
    slskd_url: str | None = Field(default=None, max_length=2048)
    slskd_api_key: str | None = Field(default=None, max_length=512)


class SpotifySettingsIn(BaseModel):
    spotify_client_id: str | None = Field(default=None, max_length=255)
    spotify_client_secret: str | None = Field(default=None, max_length=512)


class LibrarySettingsIn(BaseModel):
    music_library_path: str | None = Field(default=None, max_length=2048)
    library_path_pattern: str | None = Field(default=None, max_length=255)


class TestConnectionOut(BaseModel):
    ok: bool
    detail: str
