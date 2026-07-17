"""Admin-only routes for the database-backed integration settings store.

Auth: every route requires `require_admin` (same dependency the existing approval-queue admin
routes use in app.api.routes_requests). Secrets are never returned unmasked once saved (mission
requirement); "test connection" makes exactly one real outbound call via the existing
SafeHttpClient and never persists anything.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
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
    SpotifySettingsIn,
    SpotifySettingsOut,
    TestConnectionOut,
)
from app.services.settings_store import (
    FIELD_REGISTRY,
    get_masked_settings_view,
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
    return IntegrationSettingsOut(
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
    session: AsyncSession, settings: Settings, payload: dict[str, str | None]
) -> None:
    key = settings.resolved_settings_encryption_key()
    for field_name, value in payload.items():
        if value is None:
            continue
        _integration, _env_var, is_secret = FIELD_REGISTRY[field_name]
        await upsert_setting(
            session, field_name=field_name, value=value, key=key, is_secret=is_secret
        )
    logger.info("integration_settings_updated", fields=list(payload.keys()))


@router.put("/prowlarr", response_model=IntegrationSettingsOut)
async def update_prowlarr_settings(
    body: ProwlarrSettingsIn,
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> IntegrationSettingsOut:
    await _save_fields(session, settings, body.model_dump())
    return await get_settings_view(_admin=admin, settings=settings, session=session)


@router.put("/qbittorrent", response_model=IntegrationSettingsOut)
async def update_qbittorrent_settings(
    body: QBittorrentSettingsIn,
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> IntegrationSettingsOut:
    await _save_fields(session, settings, body.model_dump())
    return await get_settings_view(_admin=admin, settings=settings, session=session)


@router.put("/slskd", response_model=IntegrationSettingsOut)
async def update_slskd_settings(
    body: SlskdSettingsIn,
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> IntegrationSettingsOut:
    await _save_fields(session, settings, body.model_dump())
    return await get_settings_view(_admin=admin, settings=settings, session=session)


@router.put("/spotify", response_model=IntegrationSettingsOut)
async def update_spotify_settings(
    body: SpotifySettingsIn,
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> IntegrationSettingsOut:
    await _save_fields(session, settings, body.model_dump())
    return await get_settings_view(_admin=admin, settings=settings, session=session)


@router.put("/library", response_model=IntegrationSettingsOut)
async def update_library_settings(
    body: LibrarySettingsIn,
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> IntegrationSettingsOut:
    await _save_fields(session, settings, body.model_dump())
    return await get_settings_view(_admin=admin, settings=settings, session=session)


_VALID_INTEGRATIONS = {"prowlarr", "qbittorrent", "slskd", "spotify"}


@router.post("/{integration}/test", response_model=TestConnectionOut)
async def test_connection(
    integration: str,
    body: dict[str, str | None],
    _admin: User = Depends(require_admin),
) -> TestConnectionOut:
    """Test a connection using the admin-supplied values directly (not necessarily saved yet).

    Accepts a raw JSON body keyed by field name (e.g. {"prowlarr_url": "...",
    "prowlarr_api_key": "..."}) so an admin can validate credentials before saving them. Makes
    exactly one outbound call via the SSRF-hardened SafeHttpClient per the mission's carried-
    over constraint; never persists anything regardless of outcome.
    """
    if integration not in _VALID_INTEGRATIONS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown integration"
        )

    if integration == "prowlarr":
        ok, detail = await test_prowlarr(
            body.get("prowlarr_url"), body.get("prowlarr_api_key")
        )
    elif integration == "qbittorrent":
        ok, detail = await test_qbittorrent(
            body.get("qbittorrent_url"),
            body.get("qbittorrent_username"),
            body.get("qbittorrent_password"),
        )
    elif integration == "slskd":
        ok, detail = await test_slskd(body.get("slskd_url"), body.get("slskd_api_key"))
    else:
        ok, detail = await test_spotify(
            body.get("spotify_client_id"), body.get("spotify_client_secret")
        )

    return TestConnectionOut(ok=ok, detail=detail)
