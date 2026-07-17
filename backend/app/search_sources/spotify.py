"""Spotify Web API adapter, Client Credentials flow (app-only, no per-user login).

Registered once under the operator's own Spotify developer account (SPOTIFY_CLIENT_ID /
SPOTIFY_CLIENT_SECRET). The access token is cached in-process and refreshed on expiry; never
sent to or stored in the browser. This is intentionally separate from the optional per-user
Spotify *personalization* tab (Authorization Code flow), which lives in
app.services.spotify_oauth and is additive, never a replacement for this search tab.
"""

from __future__ import annotations

import asyncio
import base64
import time

from app.core.safe_http import POLICY_PUBLIC_ONLY, SafeHttpClient
from app.schemas.search import TrackResult
from app.search_sources.base import SearchSource

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"


class SpotifyAppTokenCache:
    """Caches the app-only Client Credentials token until shortly before it expires."""

    def __init__(
        self, client_id: str, client_secret: str, http: SafeHttpClient | None = None
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or SafeHttpClient()
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        async with self._lock:
            if self._token and time.monotonic() < self._expires_at - 30:
                return self._token
            basic = base64.b64encode(
                f"{self._client_id}:{self._client_secret}".encode()
            ).decode()
            resp = await self._http.post(
                SPOTIFY_TOKEN_URL,
                policy=POLICY_PUBLIC_ONLY,
                data={"grant_type": "client_credentials"},
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            self._token = payload["access_token"]
            self._expires_at = time.monotonic() + payload.get("expires_in", 3600)
            return self._token


class SpotifySearchSource(SearchSource):
    name = "spotify"

    def __init__(
        self,
        client_id: str | None,
        client_secret: str | None,
        http: SafeHttpClient | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or SafeHttpClient()
        self._token_cache: SpotifyAppTokenCache | None = None
        if client_id and client_secret:
            self._token_cache = SpotifyAppTokenCache(
                client_id, client_secret, self._http
            )

    @property
    def configured(self) -> bool:
        return self._token_cache is not None

    async def _search(self, query: str, *, limit: int = 20) -> list[TrackResult]:
        if not self._token_cache:
            return []
        token = await self._token_cache.get_token()
        resp = await self._http.get(
            SPOTIFY_SEARCH_URL,
            policy=POLICY_PUBLIC_ONLY,
            params={"q": query, "type": "track", "limit": str(min(limit, 50))},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        results: list[TrackResult] = []
        for item in data.get("tracks", {}).get("items", []):
            artists = item.get("artists") or []
            artist_name = artists[0]["name"] if artists else "Unknown artist"
            album = item.get("album") or {}
            images = album.get("images") or []
            artwork = images[0]["url"] if images else None
            release_date = album.get("release_date", "")
            year = None
            if release_date and len(release_date) >= 4 and release_date[:4].isdigit():
                year = int(release_date[:4])
            duration_ms = item.get("duration_ms")
            results.append(
                TrackResult(
                    source=self.name,
                    source_id=item["id"],
                    title=item.get("name") or "Unknown title",
                    artist=artist_name,
                    album=album.get("name"),
                    duration_seconds=(duration_ms // 1000) if duration_ms else None,
                    artwork_url=artwork,
                    year=year,
                )
            )
        return results

    async def run(self, query: str, *, limit: int = 20):
        if not self.configured:
            return await self.not_configured()
        return await super().run(query, limit=limit)
