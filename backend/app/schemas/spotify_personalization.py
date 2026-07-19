"""Pydantic models for the opt-in per-user Spotify personalization tab.

Kept separate from app.schemas.search: the app-only search tab's TrackResult is reused as the
shape of a quick-pick item here (same downstream request-creation flow, source="spotify"), but
connection status and playlist listings are personalization-specific and have nothing to do
with a search query.
"""

from __future__ import annotations

from pydantic import BaseModel


class SpotifyConnectionStatus(BaseModel):
    connected: bool
    display_name: str | None = None


class SpotifyPlaylistSummary(BaseModel):
    id: str
    name: str
    track_count: int


class SpotifyPlaylistsResponse(BaseModel):
    playlists: list[SpotifyPlaylistSummary]
