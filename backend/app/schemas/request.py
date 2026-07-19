from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.request import RequestStatus

# MusicBrainz MBIDs are always UUIDs. Strict validation here prevents a crafted string (e.g.
# containing "../") from being raw-interpolated into the MusicBrainz lookup URL path later in
# app.search_sources.musicbrainz.lookup_recording, which would otherwise let a client redirect
# that request to an arbitrary MusicBrainz API path.
_MBID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


class CreateRequestIn(BaseModel):
    source: str = Field(min_length=1, max_length=32)
    source_id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    artist: str = Field(min_length=1, max_length=500)
    album: str | None = Field(default=None, max_length=500)
    duration_seconds: int | None = Field(default=None, ge=0, le=36000)
    artwork_url: str | None = Field(default=None, max_length=2048)
    musicbrainz_recording_id: str | None = Field(default=None, max_length=64)
    preferred_format: str = Field(default="any", max_length=8)

    @field_validator("source")
    @classmethod
    def _valid_source(cls, v: str) -> str:
        allowed = {"apple", "musicbrainz", "spotify", "prowlarr"}
        if v not in allowed:
            raise ValueError(f"source must be one of {sorted(allowed)}")
        return v

    @field_validator("preferred_format")
    @classmethod
    def _valid_preferred_format(cls, v: str) -> str:
        from app.services.scoring import PREFERRED_FORMATS

        if v not in PREFERRED_FORMATS:
            raise ValueError(
                f"preferred_format must be one of {list(PREFERRED_FORMATS)}"
            )
        return v

    @field_validator("title", "artist", "album")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        return v or None

    @field_validator("musicbrainz_recording_id")
    @classmethod
    def _valid_mbid(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _MBID_RE.match(v):
            raise ValueError(
                "musicbrainz_recording_id must be a valid MusicBrainz MBID"
            )
        return v.lower()


class FulfillmentAttemptOut(BaseModel):
    id: str
    backend: str
    query_used: str
    candidates_considered: list | None
    chosen_candidate: dict | None
    outcome: str
    detail: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RequestOut(BaseModel):
    id: str
    user_id: str
    title: str
    artist: str
    album: str | None
    duration_seconds: int | None
    artwork_url: str | None
    source: str
    source_id: str
    musicbrainz_recording_id: str | None
    preferred_format: str
    status: RequestStatus
    admin_note: str | None
    failure_reason: str | None
    final_file_path: str | None
    fulfillment_attempts_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RequestDetailOut(RequestOut):
    attempts: list[FulfillmentAttemptOut] = Field(default_factory=list)


class ApproveRequestIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class RejectRequestIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)
