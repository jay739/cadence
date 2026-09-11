"""Pydantic models for the multi-source search API.

Every upstream source (Apple/iTunes, MusicBrainz, Spotify, Prowlarr) has a wildly different
response shape. Each search_sources adapter normalizes into TrackResult below, so the web
client, request flow, and tests only ever deal with one shape.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TrackResult(BaseModel):
    """One normalized search hit, regardless of which upstream source produced it."""

    source: str = Field(description="apple | musicbrainz | spotify | prowlarr")
    source_id: str
    title: str
    artist: str
    album: str | None = None
    duration_seconds: int | None = None
    artwork_url: str | None = None
    year: int | None = None
    musicbrainz_recording_id: str | None = None
    # Prowlarr-specific raw-release fields; None for canonical-metadata sources.
    indexer: str | None = None
    size_bytes: int | None = None
    seeders: int | None = None
    download_url: str | None = None
    guid: str | None = None


class SourceSearchResult(BaseModel):
    """Wraps one source's outcome so a failure never has to be inferred from an empty list."""

    source: str
    ok: bool
    error: str | None = None
    results: list[TrackResult] = Field(default_factory=list)


class MultiSearchResponse(BaseModel):
    query: str
    sources: list[SourceSearchResult]


class ResolveLinkRequest(BaseModel):
    """Body for POST /api/search/resolve-link: a pasted direct track URL (Spotify or Apple
    Music) to resolve to one exact TrackResult, as an alternative to free-text search.
    """

    url: str = Field(min_length=1, max_length=2048)
