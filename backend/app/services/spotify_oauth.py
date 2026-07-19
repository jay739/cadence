"""Per-user Spotify personalization: Authorization Code OAuth flow (opt-in, additive).

Separate from `app.search_sources.spotify`'s Client Credentials app-only search tab. That tab
searches Spotify's whole catalog with no user context; this module lets an individual user
connect their own Spotify account so the web client can surface their saved tracks/playlists as
a quick-pick source. Registered under the same app (SPOTIFY_CLIENT_ID/SECRET, whichever of env
or DB-saved admin settings is currently effective) since the mission is explicit that this is
additive to, not a second registration alongside, the existing app-only tab.

Tokens are encrypted at rest with the exact same Fernet key/mechanism already used for
DB-stored integration secrets (`app.core.crypto`), never logged, never returned to the client
in responses (only a connected/not-connected status and display name).
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import decrypt_value, encrypt_value
from app.core.safe_http import POLICY_PUBLIC_ONLY, SafeHttpClient
from app.models.user import User
from app.schemas.search import TrackResult
from app.schemas.spotify_personalization import SpotifyPlaylistSummary

SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_ME_URL = "https://api.spotify.com/v1/me"
SPOTIFY_SAVED_TRACKS_URL = "https://api.spotify.com/v1/me/tracks"
SPOTIFY_PLAYLISTS_URL = "https://api.spotify.com/v1/me/playlists"

# Read-only scopes: nothing here can modify the user's Spotify library or account.
SPOTIFY_OAUTH_SCOPES = "user-library-read playlist-read-private"

# Refresh a bit before actual expiry so a request never races an about-to-expire token.
_EXPIRY_SAFETY_MARGIN_SECONDS = 60


class SpotifyOAuthNotConfiguredError(Exception):
    """Raised when the app-level Spotify client id/secret aren't set (env or DB)."""


class SpotifyOAuthError(Exception):
    """Raised for any failure exchanging/refreshing a token with Spotify's own API."""


def build_redirect_uri(settings: Settings) -> str:
    if not settings.app_base_url:
        raise SpotifyOAuthNotConfiguredError(
            "APP_BASE_URL must be set to build the Spotify OAuth redirect URI"
        )
    return f"{settings.app_base_url.rstrip('/')}/api/spotify/callback"


def build_authorize_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": SPOTIFY_OAUTH_SCOPES,
    }
    return f"{SPOTIFY_AUTHORIZE_URL}?{urlencode(params)}"


def _basic_auth_header(client_id: str, client_secret: str) -> dict[str, str]:
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    return {
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


async def exchange_code_for_tokens(
    *,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    http: SafeHttpClient,
) -> dict:
    resp = await http.post(
        SPOTIFY_TOKEN_URL,
        policy=POLICY_PUBLIC_ONLY,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers=_basic_auth_header(client_id, client_secret),
    )
    if resp.status_code != 200:
        raise SpotifyOAuthError(
            f"Spotify token exchange failed: HTTP {resp.status_code}"
        )
    return resp.json()


async def refresh_access_token(
    *,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    http: SafeHttpClient,
) -> dict:
    resp = await http.post(
        SPOTIFY_TOKEN_URL,
        policy=POLICY_PUBLIC_ONLY,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        headers=_basic_auth_header(client_id, client_secret),
    )
    if resp.status_code != 200:
        raise SpotifyOAuthError(
            f"Spotify token refresh failed: HTTP {resp.status_code}"
        )
    return resp.json()


def store_tokens(
    user: User,
    token_payload: dict,
    *,
    key: bytes,
) -> None:
    """Encrypt and attach token fields from a Spotify token response onto a User instance.

    Does not commit; callers are expected to be inside a session that will flush/commit.
    Spotify's refresh grant does not always return a new refresh_token (it's reused until
    revoked), so an absent one here must not overwrite the previously stored refresh token.
    """
    access_token = token_payload["access_token"]
    expires_in = token_payload.get("expires_in", 3600)
    user.spotify_access_token = encrypt_value(access_token, key=key)
    user.spotify_token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    new_refresh_token = token_payload.get("refresh_token")
    if new_refresh_token:
        user.spotify_refresh_token = encrypt_value(new_refresh_token, key=key)


def disconnect(user: User) -> None:
    user.spotify_access_token = None
    user.spotify_refresh_token = None
    user.spotify_token_expires_at = None


def is_connected(user: User) -> bool:
    return user.spotify_refresh_token is not None


async def get_valid_access_token(
    user: User,
    *,
    client_id: str,
    client_secret: str,
    key: bytes,
    http: SafeHttpClient,
    session: AsyncSession,
) -> str | None:
    """Return a usable access token for this user, refreshing it first if needed.

    Returns None if the user has never connected Spotify. Refreshing updates and commits the
    user's stored tokens so the next call can reuse the still-valid access token without
    another round trip to Spotify.
    """
    if user.spotify_refresh_token is None:
        return None

    now = datetime.now(UTC)
    expires_at = user.spotify_token_expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        # SQLite has no native datetime type: SQLAlchemy round-trips DateTime(timezone=True)
        # values as naive once reloaded from a real (non in-memory-within-the-same-object)
        # row, even though they were written as UTC-aware. Reattach UTC rather than compare
        # naive-vs-aware, which raises TypeError.
        expires_at = expires_at.replace(tzinfo=UTC)
    still_valid = (
        user.spotify_access_token is not None
        and expires_at is not None
        and expires_at > now + timedelta(seconds=_EXPIRY_SAFETY_MARGIN_SECONDS)
    )
    if still_valid:
        assert user.spotify_access_token is not None
        return decrypt_value(user.spotify_access_token, key=key)

    refresh_token = decrypt_value(user.spotify_refresh_token, key=key)
    payload = await refresh_access_token(
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        http=http,
    )
    store_tokens(user, payload, key=key)
    await session.commit()
    return decrypt_value(user.spotify_access_token, key=key)  # type: ignore[arg-type]


async def fetch_display_name(access_token: str, *, http: SafeHttpClient) -> str | None:
    resp = await http.get(
        SPOTIFY_ME_URL,
        policy=POLICY_PUBLIC_ONLY,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if resp.status_code != 200:
        return None
    data = resp.json()
    return data.get("display_name") or data.get("id")


def _track_result_from_spotify_item(item: dict) -> TrackResult | None:
    track = item.get("track") if "track" in item else item
    if not track or not track.get("id"):
        return None
    artists = track.get("artists") or []
    artist_name = artists[0]["name"] if artists else "Unknown artist"
    album = track.get("album") or {}
    images = album.get("images") or []
    artwork = images[0]["url"] if images else None
    release_date = album.get("release_date", "")
    year = None
    if release_date and len(release_date) >= 4 and release_date[:4].isdigit():
        year = int(release_date[:4])
    duration_ms = track.get("duration_ms")
    return TrackResult(
        source="spotify",
        source_id=track["id"],
        title=track.get("name") or "Unknown title",
        artist=artist_name,
        album=album.get("name"),
        duration_seconds=(duration_ms // 1000) if duration_ms else None,
        artwork_url=artwork,
        year=year,
    )


async def fetch_saved_tracks(
    access_token: str, *, http: SafeHttpClient, limit: int = 30
) -> list[TrackResult]:
    resp = await http.get(
        SPOTIFY_SAVED_TRACKS_URL,
        policy=POLICY_PUBLIC_ONLY,
        params={"limit": str(min(limit, 50))},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if resp.status_code != 200:
        raise SpotifyOAuthError(
            f"Failed to fetch saved tracks: HTTP {resp.status_code}"
        )
    items = resp.json().get("items", [])
    results = [_track_result_from_spotify_item(item) for item in items]
    return [r for r in results if r is not None]


async def fetch_playlists(
    access_token: str, *, http: SafeHttpClient, limit: int = 30
) -> list[SpotifyPlaylistSummary]:
    resp = await http.get(
        SPOTIFY_PLAYLISTS_URL,
        policy=POLICY_PUBLIC_ONLY,
        params={"limit": str(min(limit, 50))},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if resp.status_code != 200:
        raise SpotifyOAuthError(f"Failed to fetch playlists: HTTP {resp.status_code}")
    items = resp.json().get("items", [])
    return [
        SpotifyPlaylistSummary(
            id=item["id"],
            name=item.get("name") or "Untitled playlist",
            track_count=(item.get("tracks") or {}).get("total", 0),
        )
        for item in items
        if item and item.get("id")
    ]


async def fetch_playlist_tracks(
    playlist_id: str, access_token: str, *, http: SafeHttpClient, limit: int = 50
) -> list[TrackResult]:
    resp = await http.get(
        f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks",
        policy=POLICY_PUBLIC_ONLY,
        params={"limit": str(min(limit, 100))},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if resp.status_code != 200:
        raise SpotifyOAuthError(
            f"Failed to fetch playlist tracks: HTTP {resp.status_code}"
        )
    items = resp.json().get("items", [])
    results = [_track_result_from_spotify_item(item) for item in items]
    return [r for r in results if r is not None]
