"""Regression tests for a real finding from the mission's adversarial review pass: a crafted
musicbrainz_recording_id could be raw-interpolated into the MusicBrainz lookup URL path,
letting a client redirect that outbound request to an arbitrary MusicBrainz API path (e.g.
"../../artist/x"). Fixed at two layers: CreateRequestIn's Pydantic validator (input boundary)
and lookup_recording's own defense-in-depth check (point of use).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.request import CreateRequestIn
from app.search_sources.musicbrainz import lookup_recording

VALID_MBID = "e37b1eb1-1b8e-4f0a-9c6e-1e6b7e7f8a2b"


def _body(**overrides):
    defaults = {
        "source": "musicbrainz",
        "source_id": "track-1",
        "title": "Tum Hi Ho",
        "artist": "Arijit Singh",
    }
    defaults.update(overrides)
    return defaults


class TestCreateRequestInMbidValidation:
    def test_valid_mbid_accepted(self):
        body = CreateRequestIn(**_body(musicbrainz_recording_id=VALID_MBID))
        assert body.musicbrainz_recording_id == VALID_MBID

    def test_none_is_accepted(self):
        body = CreateRequestIn(**_body(musicbrainz_recording_id=None))
        assert body.musicbrainz_recording_id is None

    def test_uppercase_mbid_normalized_to_lowercase(self):
        body = CreateRequestIn(**_body(musicbrainz_recording_id=VALID_MBID.upper()))
        assert body.musicbrainz_recording_id == VALID_MBID.lower()

    @pytest.mark.parametrize(
        "malicious_id",
        [
            "../../../v2/artist/x",
            "../../release/y",
            "..%2f..%2fartist",
            "not-a-uuid-at-all",
            "e37b1eb1-1b8e-4f0a-9c6e-1e6b7e7f8a2b/../../artist",
            "e37b1eb1-1b8e-4f0a-9c6e-1e6b7e7f8a2b/extra",
            "",
        ],
    )
    def test_malicious_or_malformed_ids_rejected(self, malicious_id):
        with pytest.raises(ValidationError):
            CreateRequestIn(**_body(musicbrainz_recording_id=malicious_id))


class TestLookupRecordingDefenseInDepth:
    @pytest.mark.asyncio
    async def test_rejects_path_traversal_without_making_any_request(self):
        # Passing a non-UUID directly to lookup_recording (bypassing the Pydantic layer
        # entirely) must still be refused, and must never issue an HTTP request.
        result = await lookup_recording("../../../v2/artist/x")
        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_empty_string(self):
        result = await lookup_recording("")
        assert result is None
