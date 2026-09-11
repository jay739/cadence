from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_db
from app.core.config import Settings
from app.core.safe_http import POLICY_ALLOW_PRIVATE, SafeHttpClient, SafeHttpError
from app.schemas.health import HealthOut, IntegrationStatus
from app.services.settings_store import apply_effective_settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["health"])


async def _reachable(url: str, headers: dict | None = None) -> bool:
    client = SafeHttpClient(timeout_seconds=3.0, max_retries=0)
    try:
        resp = await client.get(url, policy=POLICY_ALLOW_PRIVATE, headers=headers)
        return resp.status_code < 500
    except SafeHttpError:
        return False


@router.get("/health", response_model=HealthOut)
async def health(
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> HealthOut:
    """Version, mode, DB status, and per-integration configured/reachable booleans.

    Never leaks URLs, keys, or filesystem paths, per the mission's explicit requirement.
    """
    database_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        database_ok = False

    # DB-stored admin settings must be reflected here too (not just env vars), since /api/health
    # is how the rest of the app (and any future native client) knows what is actually usable.
    if not settings.demo_mode and database_ok:
        settings = await apply_effective_settings(session, settings)

    integrations: dict[str, IntegrationStatus] = {
        "prowlarr": IntegrationStatus(configured=settings.prowlarr_configured),
        "qbittorrent": IntegrationStatus(configured=settings.qbittorrent_configured),
        "slskd": IntegrationStatus(configured=settings.slskd_configured),
        "spotify": IntegrationStatus(configured=settings.spotify_configured),
        # Read pre-login, by the login page, to decide whether a "sign in with Navidrome"
        # option is even offered. Just a boolean: no host or credential detail, same as
        # every other entry here.
        "navidrome": IntegrationStatus(configured=bool(settings.navidrome_url)),
        "ytdlp": IntegrationStatus(configured=settings.ytdlp_shim_configured),
    }

    if not settings.demo_mode:
        if settings.navidrome_url:
            integrations["navidrome"].reachable = await _reachable(
                f"{settings.navidrome_url.rstrip('/')}/rest/ping.view"
            )
        if settings.prowlarr_configured and settings.prowlarr_url:
            integrations["prowlarr"].reachable = await _reachable(
                f"{settings.prowlarr_url.rstrip('/')}/api/v1/system/status",
                headers={"X-Api-Key": settings.prowlarr_api_key},
            )
        if settings.qbittorrent_configured and settings.qbittorrent_url:
            integrations["qbittorrent"].reachable = await _reachable(
                f"{settings.qbittorrent_url.rstrip('/')}/api/v2/app/version"
            )
        if settings.slskd_configured and settings.slskd_url:
            integrations["slskd"].reachable = await _reachable(
                f"{settings.slskd_url.rstrip('/')}/api/v0/session",
                headers={"X-API-Key": settings.slskd_api_key},
            )
        if settings.ytdlp_shim_configured and settings.ytdlp_shim_url:
            integrations["ytdlp"].reachable = await _reachable(
                f"{settings.ytdlp_shim_url.rstrip('/')}/health"
            )

    return HealthOut(
        version=settings.version,
        mode="demo" if settings.demo_mode else "live",
        database_ok=database_ok,
        integrations=integrations,
    )
