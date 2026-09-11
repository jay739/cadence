"""Admin-editable, database-backed integration settings: precedence, refresh, and CRUD.

Precedence rule (documented per the mission's requirement): **an environment variable, when
set, always wins over any DB-stored value for that same field.** DB-stored values are the
fallback/normal runtime path used whenever the env var is unset. This direction was chosen
over "DB overrides env once present" because:
  - It matches the mission's framing of env vars as the "bootstrap/override path": an operator
    who sets an env var is making an explicit, deliberate infrastructure decision (e.g. a
    Docker secret, a compose override, a value injected by a orchestration layer they control)
    that should not be silently shadowed by whatever was last clicked in a web form.
  - It keeps existing docker-compose-only deployments byte-for-byte unaffected: nothing they do
    in the UI (indeed, nothing can be done, since this repo has no such UI today) could ever
    have raced ahead of their env vars anyway. This makes the new behavior strictly additive.
  - It gives an operator a clean escape hatch if a UI-saved value is ever wrong or its
    encryption key is lost: unset the DB row (or just ignore it) and set the env var, and the
    env var takes over immediately on the next request, no DB migration or manual row deletion
    required.

No caching: `get_effective_settings()` performs a fresh (lightweight, indexed) query every
call. This deliberately replaces `get_settings()`'s process-wide `lru_cache` for exactly the
fields listed in the mission (Prowlarr, qBittorrent, slskd, Spotify, library path/pattern) so a
save from the admin settings UI is visible on the very next request, without a restart. Every
other Settings field (SESSION_TTL_MINUTES, OUTBOUND_TIMEOUT_SECONDS, fulfillment tuning, etc.)
is untouched and keeps using the existing cached `get_settings()` path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import decrypt_value, encrypt_value, mask_secret
from app.models.integration_setting import IntegrationSetting
from app.models.integration_source_preference import IntegrationSourcePreference

# --- Field registry: the exact set the mission scopes this addition to. ---
# Each entry: field_name -> (integration, env_var_name, is_secret)
FIELD_REGISTRY: dict[str, tuple[str, str, bool]] = {
    "prowlarr_url": ("prowlarr", "PROWLARR_URL", False),
    "prowlarr_api_key": ("prowlarr", "PROWLARR_API_KEY", True),
    "qbittorrent_url": ("qbittorrent", "QBITTORRENT_URL", False),
    "qbittorrent_username": ("qbittorrent", "QBITTORRENT_USERNAME", False),
    "qbittorrent_password": ("qbittorrent", "QBITTORRENT_PASSWORD", True),
    "slskd_url": ("slskd", "SLSKD_URL", False),
    "slskd_api_key": ("slskd", "SLSKD_API_KEY", True),
    "spotify_client_id": ("spotify", "SPOTIFY_CLIENT_ID", False),
    "spotify_client_secret": ("spotify", "SPOTIFY_CLIENT_SECRET", True),
    "music_library_path": ("library", "MUSIC_LIBRARY_PATH", False),
    "library_path_pattern": ("library", "LIBRARY_PATH_PATTERN", False),
}

INTEGRATIONS = ("prowlarr", "qbittorrent", "slskd", "spotify", "library")


def fields_for_integration(integration: str) -> list[str]:
    return [f for f, (i, _, _) in FIELD_REGISTRY.items() if i == integration]


@dataclass
class EffectiveIntegrationSettings:
    """The resolved value (env-wins-if-set, else DB, else None) for every registry field."""

    values: dict[str, str | None]

    def get(self, field_name: str) -> str | None:
        return self.values.get(field_name)


async def _load_db_values(session: AsyncSession, key: bytes) -> dict[str, str | None]:
    result = await session.execute(select(IntegrationSetting))
    rows = result.scalars().all()
    out: dict[str, str | None] = {}
    for row in rows:
        if row.value is None:
            out[row.field_name] = None
            continue
        if row.is_secret:
            try:
                out[row.field_name] = decrypt_value(row.value, key=key)
            except (
                Exception
            ):  # noqa: BLE001 - a bad/rotated key must degrade, not crash
                out[row.field_name] = None
        else:
            out[row.field_name] = row.value
    return out


async def get_source_preferences(session: AsyncSession) -> dict[str, str]:
    """Per-integration choice of which source wins: "env" (default) or "database".

    A missing row means "env", which preserves the original hardcoded behavior for every
    integration that has never touched this feature.
    """
    result = await session.execute(select(IntegrationSourcePreference))
    return {row.integration: row.source for row in result.scalars().all()}


async def set_source_preference(
    session: AsyncSession, *, integration: str, source: str
) -> None:
    if integration not in INTEGRATIONS:
        raise ValueError(f"Unknown integration: {integration!r}")
    if source not in ("env", "database"):
        raise ValueError(f"Invalid source preference: {source!r}")
    result = await session.execute(
        select(IntegrationSourcePreference).where(
            IntegrationSourcePreference.integration == integration
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        session.add(IntegrationSourcePreference(integration=integration, source=source))
    else:
        row.source = source
    await session.commit()


async def get_effective_settings(
    session: AsyncSession, settings: Settings
) -> EffectiveIntegrationSettings:
    """Resolve every registry field per its integration's source preference.

    Default preference ("env", used whenever no row exists) behaves exactly as before: env var
    if set, else DB value. An integration explicitly set to "database" ignores its env var
    entirely and uses the DB value as-is (which may be None: an explicit clear, or simply never
    saved) even when an env var happens to be present -- otherwise clearing a value while in
    "database" mode could never actually take effect. `settings.settings_force_env_precedence`
    is the break-glass override (issue #18): when set, every integration is resolved as "env"
    regardless of any stored preference, so a bad DB value can never lock an operator out.
    """
    key = settings.resolved_settings_encryption_key()
    db_values = await _load_db_values(session, key)
    preferences = (
        {}
        if settings.settings_force_env_precedence
        else await get_source_preferences(session)
    )

    values: dict[str, str | None] = {}
    for field_name, (integration, env_var, _is_secret) in FIELD_REGISTRY.items():
        prefer_database = preferences.get(integration) == "database"
        env_value = os.environ.get(env_var)
        if prefer_database:
            values[field_name] = db_values.get(field_name)
        elif env_value:
            values[field_name] = env_value
        else:
            values[field_name] = db_values.get(field_name)
    return EffectiveIntegrationSettings(values=values)


async def apply_effective_settings(
    session: AsyncSession, settings: Settings
) -> Settings:
    """Return a copy of `settings` with registry fields overlaid from the effective resolver.

    Used at each read site (search fan-out, fulfillment, health) instead of touching every
    call site's plumbing: callers already receive a `Settings`-shaped object today, so this
    keeps that contract intact while making exactly the mission-scoped fields live-refreshable.

    Purely additive: a field is only overridden when the resolver actually found a value (a
    real `os.environ` entry or a DB row). If neither exists, whatever `settings` already
    carries for that field (e.g. a value passed directly to the `Settings` constructor, as
    tests and any future non-HTTP caller may do, or loaded from `.env` by pydantic-settings) is
    left untouched rather than being wiped to None.
    """
    effective = await get_effective_settings(session, settings)
    update: dict[str, str] = {
        field_name: value for field_name, value in effective.values.items() if value
    }
    return settings.model_copy(update=update) if update else settings


async def seed_db_from_env_on_boot(session: AsyncSession, settings: Settings) -> None:
    """On boot, seed any registry field's DB row from its env var, if the env var is set.

    This keeps the DB's own record in sync with whatever an operator has wired via env vars
    (env always wins at read time regardless, per the precedence rule above, but seeding the
    DB row too means the admin settings page can display "currently: <masked/plain value>,
    sourced from the environment" rather than an empty field, and means the value survives if
    the operator ever removes the env var later, e.g. to switch to UI-managed configuration
    without re-typing everything). Only overwrites the DB row when the env var is actually set;
    never clears a DB row just because an env var happens to be absent this boot.
    """
    key = settings.resolved_settings_encryption_key()
    for field_name, (_integration, env_var, is_secret) in FIELD_REGISTRY.items():
        env_value = os.environ.get(env_var)
        if not env_value:
            continue
        await upsert_setting(
            session,
            field_name=field_name,
            value=env_value,
            key=key,
            is_secret=is_secret,
        )


async def upsert_setting(
    session: AsyncSession,
    *,
    field_name: str,
    value: str | None,
    key: bytes,
    is_secret: bool,
) -> None:
    if field_name not in FIELD_REGISTRY:
        raise ValueError(f"Unknown settings field: {field_name!r}")
    integration, _env_var, _is_secret = FIELD_REGISTRY[field_name]
    result = await session.execute(
        select(IntegrationSetting).where(IntegrationSetting.field_name == field_name)
    )
    row = result.scalar_one_or_none()
    stored_value = encrypt_value(value, key=key) if (value and is_secret) else value
    if row is None:
        row = IntegrationSetting(
            integration=integration,
            field_name=field_name,
            value=stored_value,
            is_secret=is_secret,
        )
        session.add(row)
    else:
        row.value = stored_value
        row.is_secret = is_secret
    await session.commit()


@dataclass
class SourcePreferenceInfo:
    """Which source an integration is set to prefer, and whether that choice is currently
    overridden by the break-glass env var (`Settings.settings_force_env_precedence`)."""

    preference: str
    forced_env: bool


async def get_source_preference_view(
    session: AsyncSession, settings: Settings
) -> dict[str, SourcePreferenceInfo]:
    """One entry per integration, always reflecting the actual stored preference (even while
    `forced_env` is overriding its effect, so the UI can keep the saved radio selection
    visible rather than appearing to silently reset it)."""
    forced_env = settings.settings_force_env_precedence
    preferences = await get_source_preferences(session)
    return {
        integration: SourcePreferenceInfo(
            preference=preferences.get(integration, "env"), forced_env=forced_env
        )
        for integration in INTEGRATIONS
    }


async def get_masked_settings_view(
    session: AsyncSession, settings: Settings
) -> dict[str, dict[str, str | None]]:
    """Return every integration's fields for the admin GET route, secrets masked.

    Never returns a real secret value once one has been saved (mission requirement). A field
    with no effective value (neither env nor DB) is returned as None so the web client can
    show an empty input rather than a misleading placeholder.

    `<field>__source` is "env" | "db" | "unset", preference-aware: an integration set to
    "database" never reports "env" for its fields even when the env var happens to be present,
    since it is deliberately not being used (see `get_source_preference_view` for exposing that
    choice itself to callers, e.g. the settings UI).
    """
    effective = await get_effective_settings(session, settings)
    forced_env = settings.settings_force_env_precedence
    preferences = {} if forced_env else await get_source_preferences(session)

    out: dict[str, dict[str, str | None]] = {
        integration: {} for integration in INTEGRATIONS
    }
    for field_name, (integration, env_var, is_secret) in FIELD_REGISTRY.items():
        raw_value = effective.get(field_name)
        if is_secret:
            out[integration][field_name] = mask_secret(raw_value)
        else:
            out[integration][field_name] = raw_value

        prefer_database = preferences.get(integration) == "database"
        if prefer_database:
            out[integration][f"{field_name}__source"] = "db" if raw_value else "unset"
        else:
            out[integration][f"{field_name}__source"] = (
                "env" if os.environ.get(env_var) else ("db" if raw_value else "unset")
            )
    return out
