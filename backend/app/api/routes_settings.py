"""Admin-only routes for the database-backed integration settings store.

Auth: every route requires `require_admin` (same dependency the existing approval-queue admin
routes use in app.api.routes_requests). Secrets are never returned unmasked once saved (mission
requirement); "test connection" makes exactly one real outbound call via the existing
SafeHttpClient and never persists anything.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_db, require_admin
from app.core.config import Settings
from app.models.user import User
from app.schemas.settings import (
    FieldValue,
    IntegrationSettingsOut,
    LibrarySettingsIn,
    LibrarySettingsOut,
    ProwlarrSettingsIn,
    ProwlarrSettingsOut,
    QBittorrentSettingsIn,
    QBittorrentSettingsOut,
    SlskdSettingsIn,
    SlskdSettingsOut,
    SourcePreferenceIn,
    SourcePreferenceOut,
    SpotifySettingsIn,
    SpotifySettingsOut,
    TestConnectionOut,
)
from app.services.settings_store import (
    FIELD_REGISTRY,
    INTEGRATIONS,
    get_effective_settings,
    get_masked_settings_view,
    get_source_preference_view,
    set_source_preference,
    upsert_setting,
)
from app.services.settings_test_connection import (
    test_prowlarr,
    test_qbittorrent,
    test_slskd,
    test_spotify,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/admin/settings", tags=["admin-settings"])


def _field_value(masked_view: dict, integration: str, field_name: str) -> FieldValue:
    _integration, _env_var, is_secret = FIELD_REGISTRY[field_name]
    return FieldValue(
        value=masked_view[integration].get(field_name),
        source=masked_view[integration].get(f"{field_name}__source", "unset"),
        is_secret=is_secret,
    )


@router.get("", response_model=IntegrationSettingsOut)
async def get_settings_view(
    _admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> IntegrationSettingsOut:
    masked = await get_masked_settings_view(session, settings)
    preference_view = await get_source_preference_view(session, settings)
    source_preferences = {
        integration: SourcePreferenceOut(
            preference=info.preference, forced_env=info.forced_env
        )
        for integration, info in preference_view.items()
    }
    return IntegrationSettingsOut(
        source_preferences=source_preferences,
        prowlarr=ProwlarrSettingsOut(
            prowlarr_url=_field_value(masked, "prowlarr", "prowlarr_url"),
            prowlarr_api_key=_field_value(masked, "prowlarr", "prowlarr_api_key"),
        ),
        qbittorrent=QBittorrentSettingsOut(
            qbittorrent_url=_field_value(masked, "qbittorrent", "qbittorrent_url"),
            qbittorrent_username=_field_value(
                masked, "qbittorrent", "qbittorrent_username"
            ),
            qbittorrent_password=_field_value(
                masked, "qbittorrent", "qbittorrent_password"
            ),
        ),
        slskd=SlskdSettingsOut(
            slskd_url=_field_value(masked, "slskd", "slskd_url"),
            slskd_api_key=_field_value(masked, "slskd", "slskd_api_key"),
        ),
        spotify=SpotifySettingsOut(
            spotify_client_id=_field_value(masked, "spotify", "spotify_client_id"),
            spotify_client_secret=_field_value(
                masked, "spotify", "spotify_client_secret"
            ),
        ),
        library=LibrarySettingsOut(
            music_library_path=_field_value(masked, "library", "music_library_path"),
            library_path_pattern=_field_value(
                masked, "library", "library_path_pattern"
            ),
        ),
    )


async def _save_fields(
    session: AsyncSession, settings: Settings, body: BaseModel
) -> None:
    """Upsert every field the request body actually included, clearing any sent as null.

    Uses `body.model_fields_set` rather than `body.model_dump()` so an explicitly-sent
    `{"field": null}` (clear this value) is distinguishable from a field the client simply
    didn't include (leave it untouched) -- both look identical as plain `None` after
    `model_dump()`, which is exactly the bug issue #18 flagged: there was previously no way to
    clear a saved DB value through this API at all.
    """
    key = settings.resolved_settings_encryption_key()
    updated_fields = list(body.model_fields_set)
    for field_name in updated_fields:
        value = getattr(body, field_name)
        _integration, _env_var, is_secret = FIELD_REGISTRY[field_name]
        await upsert_setting(
            session, field_name=field_name, value=value, key=key, is_secret=is_secret
        )
    if updated_fields:
        logger.info("integration_settings_updated", fields=updated_fields)


@router.put("/prowlarr", response_model=IntegrationSettingsOut)
async def update_prowlarr_settings(
    body: ProwlarrSettingsIn,
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> IntegrationSettingsOut:
    await _save_fields(session, settings, body)
    return await get_settings_view(_admin=admin, settings=settings, session=session)


@router.put("/qbittorrent", response_model=IntegrationSettingsOut)
async def update_qbittorrent_settings(
    body: QBittorrentSettingsIn,
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> IntegrationSettingsOut:
    await _save_fields(session, settings, body)
    return await get_settings_view(_admin=admin, settings=settings, session=session)


@router.put("/slskd", response_model=IntegrationSettingsOut)
async def update_slskd_settings(
    body: SlskdSettingsIn,
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> IntegrationSettingsOut:
    await _save_fields(session, settings, body)
    return await get_settings_view(_admin=admin, settings=settings, session=session)


@router.put("/spotify", response_model=IntegrationSettingsOut)
async def update_spotify_settings(
    body: SpotifySettingsIn,
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> IntegrationSettingsOut:
    await _save_fields(session, settings, body)
    return await get_settings_view(_admin=admin, settings=settings, session=session)


@router.put("/library", response_model=IntegrationSettingsOut)
async def update_library_settings(
    body: LibrarySettingsIn,
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> IntegrationSettingsOut:
    await _save_fields(session, settings, body)
    return await get_settings_view(_admin=admin, settings=settings, session=session)


@router.put("/{integration}/source", response_model=IntegrationSettingsOut)
async def update_source_preference(
    integration: str,
    body: SourcePreferenceIn,
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> IntegrationSettingsOut:
    """Set whether `integration` prefers "env" or "database" (issue #18).

    Saving is always allowed, even while `SETTINGS_FORCE_ENV_PRECEDENCE` is currently
    overriding its effect -- the choice takes effect the moment that break-glass override is
    lifted, without needing to be re-saved.
    """
    if integration not in INTEGRATIONS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown integration"
        )
    if body.preference not in ("env", "database"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='preference must be "env" or "database"',
        )
    await set_source_preference(
        session, integration=integration, source=body.preference
    )
    logger.info(
        "integration_source_preference_updated",
        integration=integration,
        preference=body.preference,
    )
    return await get_settings_view(_admin=admin, settings=settings, session=session)


_VALID_INTEGRATIONS = {"prowlarr", "qbittorrent", "slskd", "spotify"}


@router.post("/{integration}/test", response_model=TestConnectionOut)
async def test_connection(
    integration: str,
    body: dict[str, str | None],
    _admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> TestConnectionOut:
    """Test a connection, using whatever the admin has typed and falling back to the value
    already in effect for anything they haven't.

    Accepts a raw JSON body keyed by field name (e.g. {"prowlarr_url": "...",
    "prowlarr_api_key": "..."}) so an admin can validate credentials before saving them. Makes
    exactly one outbound call via the SSRF-hardened SafeHttpClient per the mission's carried-
    over constraint; never persists anything regardless of outcome.

    The fallback is not a convenience, it is what makes the button work at all on a normally
    configured install. Secrets are deliberately never sent to the browser (only a mask), and
    an env-sourced field is rendered disabled, so for those two cases the client has nothing
    it *could* put in the body: testing an env-configured Prowlarr always reported "URL and
    API key are both required" because the request genuinely carried neither.
    """
    if integration not in _VALID_INTEGRATIONS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown integration"
        )

    effective = await get_effective_settings(session, settings)

    def field(name: str) -> str | None:
        """Typed-in value if the admin supplied a non-blank one, else what's in effect."""
        supplied = body.get(name)
        if supplied is not None and supplied.strip():
            return supplied
        return effective.get(name)

    if integration == "prowlarr":
        ok, detail = await test_prowlarr(
            field("prowlarr_url"), field("prowlarr_api_key")
        )
    elif integration == "qbittorrent":
        ok, detail = await test_qbittorrent(
            field("qbittorrent_url"),
            field("qbittorrent_username"),
            field("qbittorrent_password"),
        )
    elif integration == "slskd":
        ok, detail = await test_slskd(field("slskd_url"), field("slskd_api_key"))
    else:
        ok, detail = await test_spotify(
            field("spotify_client_id"), field("spotify_client_secret")
        )

    return TestConnectionOut(ok=ok, detail=detail)
