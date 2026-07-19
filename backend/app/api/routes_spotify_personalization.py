"""Opt-in per-user Spotify personalization: connect, disconnect, status, and quick-pick browse.

Every route here requires authentication (a logged-in Cadence user), separate from the
app-only /api/search Spotify tab which needs no user context at all. The redirect-based OAuth
dance (connect -> Spotify -> callback) can't carry a Bearer token across the browser's
top-level navigation to Spotify and back, so /connect and /callback authenticate via the
session cookie (already set by login) rather than the Authorization header the SPA otherwise
uses; the signed `state` param additionally binds the callback to the user who started it.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_current_user, get_db
from app.core.config import Settings
from app.core.limiter import (
    SPOTIFY_CONNECTION_RATE_LIMIT,
    SPOTIFY_LIBRARY_RATE_LIMIT,
    limiter,
)
from app.core.safe_http import SafeHttpClient
from app.core.security import (
    OAuthStateError,
    create_oauth_state_token,
    decode_oauth_state_token,
)
from app.models.user import User
from app.schemas.search import TrackResult
from app.schemas.spotify_personalization import (
    SpotifyConnectionStatus,
    SpotifyPlaylistsResponse,
)
from app.services.settings_store import apply_effective_settings
from app.services.spotify_oauth import (
    SpotifyOAuthError,
    SpotifyOAuthNotConfiguredError,
    build_authorize_url,
    build_redirect_uri,
    disconnect,
    exchange_code_for_tokens,
    fetch_display_name,
    fetch_playlist_tracks,
    fetch_playlists,
    fetch_saved_tracks,
    get_valid_access_token,
    is_connected,
    store_tokens,
)
from app.services.users import get_user_by_id

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/spotify", tags=["spotify-personalization"])

# Spotify's own IDs are base62, 22 characters; constrains the path param before it ever reaches
# an outbound call, even though the host is fixed and not attacker-influenceable regardless.
_SPOTIFY_ID_PATTERN = r"^[A-Za-z0-9]{1,64}$"


async def _resolved_settings(session: AsyncSession, settings: Settings) -> Settings:
    return await apply_effective_settings(session, settings)


@router.get("/status", response_model=SpotifyConnectionStatus)
@limiter.limit(SPOTIFY_LIBRARY_RATE_LIMIT)
async def spotify_status(
    request: Request,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> SpotifyConnectionStatus:
    if not is_connected(user):
        return SpotifyConnectionStatus(connected=False)

    effective = await _resolved_settings(session, settings)
    if not (effective.spotify_client_id and effective.spotify_client_secret):
        return SpotifyConnectionStatus(connected=True)

    try:
        token = await get_valid_access_token(
            user,
            client_id=effective.spotify_client_id,
            client_secret=effective.spotify_client_secret,
            key=settings.resolved_settings_encryption_key(),
            http=SafeHttpClient(),
            session=session,
        )
    except SpotifyOAuthError:
        return SpotifyConnectionStatus(connected=True)

    display_name = None
    if token:
        display_name = await fetch_display_name(token, http=SafeHttpClient())
    return SpotifyConnectionStatus(connected=True, display_name=display_name)


@router.get("/connect")
@limiter.limit(SPOTIFY_CONNECTION_RATE_LIMIT)
async def spotify_connect(
    request: Request,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    effective = await _resolved_settings(session, settings)
    if not (effective.spotify_client_id and effective.spotify_client_secret):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Spotify is not configured by the operator yet",
        )
    try:
        redirect_uri = build_redirect_uri(effective)
    except SpotifyOAuthNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    state = create_oauth_state_token(
        user_id=user.id, secret=settings.resolved_session_secret()
    )
    authorize_url = build_authorize_url(
        client_id=effective.spotify_client_id,
        redirect_uri=redirect_uri,
        state=state,
    )
    return RedirectResponse(authorize_url, status_code=status.HTTP_302_FOUND)


@router.get("/callback")
async def spotify_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    if error or not code or not state:
        return RedirectResponse(
            "/?spotify_connect=error", status_code=status.HTTP_302_FOUND
        )

    try:
        user_id = decode_oauth_state_token(
            state, secret=settings.resolved_session_secret()
        )
    except OAuthStateError:
        return RedirectResponse(
            "/?spotify_connect=error", status_code=status.HTTP_302_FOUND
        )

    user = await get_user_by_id(session, user_id)
    if user is None:
        return RedirectResponse(
            "/?spotify_connect=error", status_code=status.HTTP_302_FOUND
        )

    effective = await _resolved_settings(session, settings)
    if not (effective.spotify_client_id and effective.spotify_client_secret):
        return RedirectResponse(
            "/?spotify_connect=error", status_code=status.HTTP_302_FOUND
        )

    try:
        redirect_uri = build_redirect_uri(effective)
        payload = await exchange_code_for_tokens(
            code=code,
            redirect_uri=redirect_uri,
            client_id=effective.spotify_client_id,
            client_secret=effective.spotify_client_secret,
            http=SafeHttpClient(),
        )
    except (SpotifyOAuthNotConfiguredError, SpotifyOAuthError) as exc:
        logger.warning("spotify_oauth_callback_failed", error=str(exc))
        return RedirectResponse(
            "/?spotify_connect=error", status_code=status.HTTP_302_FOUND
        )

    store_tokens(user, payload, key=settings.resolved_settings_encryption_key())
    await session.commit()
    return RedirectResponse(
        "/?spotify_connect=success", status_code=status.HTTP_302_FOUND
    )


@router.post("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(SPOTIFY_CONNECTION_RATE_LIMIT)
async def spotify_disconnect(
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> None:
    disconnect(user)
    await session.commit()


async def _access_token_or_404(
    user: User, settings: Settings, session: AsyncSession
) -> str:
    effective = await _resolved_settings(session, settings)
    if not (effective.spotify_client_id and effective.spotify_client_secret):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Spotify is not configured by the operator",
        )
    try:
        token = await get_valid_access_token(
            user,
            client_id=effective.spotify_client_id,
            client_secret=effective.spotify_client_secret,
            key=settings.resolved_settings_encryption_key(),
            http=SafeHttpClient(),
            session=session,
        )
    except SpotifyOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach Spotify to refresh your connection",
        ) from exc
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Spotify is not connected for this user",
        )
    return token


@router.get("/library/saved-tracks", response_model=list[TrackResult])
@limiter.limit(SPOTIFY_LIBRARY_RATE_LIMIT)
async def spotify_saved_tracks(
    request: Request,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> list[TrackResult]:
    token = await _access_token_or_404(user, settings, session)
    return await fetch_saved_tracks(token, http=SafeHttpClient())


@router.get("/library/playlists", response_model=SpotifyPlaylistsResponse)
@limiter.limit(SPOTIFY_LIBRARY_RATE_LIMIT)
async def spotify_playlists(
    request: Request,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> SpotifyPlaylistsResponse:
    token = await _access_token_or_404(user, settings, session)
    playlists = await fetch_playlists(token, http=SafeHttpClient())
    return SpotifyPlaylistsResponse(playlists=playlists)


@router.get("/library/playlists/{playlist_id}/tracks", response_model=list[TrackResult])
@limiter.limit(SPOTIFY_LIBRARY_RATE_LIMIT)
async def spotify_playlist_tracks(
    request: Request,
    playlist_id: str = Path(..., pattern=_SPOTIFY_ID_PATTERN),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> list[TrackResult]:
    token = await _access_token_or_404(user, settings, session)
    return await fetch_playlist_tracks(playlist_id, token, http=SafeHttpClient())
