"""Request/response shapes for the already-in-library check.

Mirrored in the frontend at web/src/types/api.ts.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LibraryCheckItem(BaseModel):
    """One track to look up.

    `key` is whatever the client uses to index its own results (the frontend sends
    `"{source}:{source_id}"`), echoed back untouched so the caller never has to re-derive the
    correspondence.
    """

    key: str = Field(min_length=1, max_length=512)
    title: str = Field(min_length=1, max_length=500)
    artist: str = Field(min_length=1, max_length=500)


class LibraryCheckRequest(BaseModel):
    # Capped at the page size the search UI can actually show, so one call cannot be turned into
    # a large fan-out against the operator's Navidrome.
    tracks: list[LibraryCheckItem] = Field(default_factory=list, max_length=50)


class LibraryMatch(BaseModel):
    """A confident hit. The Navidrome metadata is returned so the UI can say what it matched."""

    key: str
    navidrome_id: str
    title: str
    artist: str
    album: str | None = None


class LibraryCheckResponse(BaseModel):
    """Only confident hits are listed; a key's absence means "not confidently found".

    `available` distinguishes a real answer from no answer. It is False when no Navidrome
    service account is configured, in demo mode, or when every lookup failed -- and in that case
    the client must render nothing at all rather than implying the tracks are absent.
    """

    available: bool
    matches: list[LibraryMatch] = Field(default_factory=list)
