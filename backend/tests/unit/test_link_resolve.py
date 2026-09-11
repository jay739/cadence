"""Unit tests for parsing a pasted Spotify/Apple Music track link and dispatching the by-id
lookup to the matching adapter.

Adapter-level HTTP mocking (the actual iTunes Lookup / Spotify Get-Several-Tracks calls)
already lives in test_search_sources.py; these tests isolate link_resolve.py's own logic (URL
parsing into platform + native id, and the not-configured/not-found error translation) by
monkeypatching the adapters' fetch_by_id directly rather than mocking HTTP again.
"""

from __future__ import annotations

import pytest

from app.schemas.search import TrackResult
from app.search_sources.apple import AppleSearchSource
from app.search_sources.spotify import SpotifySearchSource
from app.services.link_resolve import (
    TrackLinkNotFoundError,
    TrackLinkSourceNotConfiguredError,
    UnrecognizedTrackLinkError,
    fetch_track_by_id,
    parse_track_link,
)


class TestParseTrackLinkSpotify:
    def test_bare_track_link(self):
        platform, native_id = parse_track_link(
            "https://open.spotify.com/track/6y0igZArWVi6Iz0rj35c1Y"
        )
        assert platform == "spotify"
        assert native_id == "6y0igZArWVi6Iz0rj35c1Y"

    def test_track_link_with_share_query_param(self):
        platform, native_id = parse_track_link(
            "https://open.spotify.com/track/6y0igZArWVi6Iz0rj35c1Y?si=deadbeef"
        )
        assert platform == "spotify"
        assert native_id == "6y0igZArWVi6Iz0rj35c1Y"

    def test_track_link_with_locale_prefix(self):
        platform, native_id = parse_track_link(
            "https://open.spotify.com/intl-en/track/6y0igZArWVi6Iz0rj35c1Y"
        )
        assert platform == "spotify"
        assert native_id == "6y0igZArWVi6Iz0rj35c1Y"

    def test_playlist_link_is_rejected(self):
        with pytest.raises(UnrecognizedTrackLinkError):
            parse_track_link("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")

    def test_album_link_is_rejected(self):
        with pytest.raises(UnrecognizedTrackLinkError):
            parse_track_link("https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3")


class TestParseTrackLinkAppleMusic:
    def test_song_link(self):
        platform, native_id = parse_track_link(
            "https://music.apple.com/us/song/tum-hi-ho/643421652"
        )
        assert platform == "apple"
        assert native_id == "643421652"

    def test_album_link_with_track_query_param(self):
        platform, native_id = parse_track_link(
            "https://music.apple.com/in/album/aashiqui-2/643421641?i=643421652"
        )
        assert platform == "apple"
        assert native_id == "643421652"

    def test_bare_album_link_without_track_param_is_rejected(self):
        with pytest.raises(UnrecognizedTrackLinkError):
            parse_track_link("https://music.apple.com/us/album/aashiqui-2/643421641")

    def test_artist_link_is_rejected(self):
        with pytest.raises(UnrecognizedTrackLinkError):
            parse_track_link("https://music.apple.com/us/artist/arijit-singh/393214216")


class TestParseTrackLinkRejectsEverythingElse:
    def test_unrelated_host(self):
        with pytest.raises(UnrecognizedTrackLinkError):
            parse_track_link("https://youtube.com/watch?v=dQw4w9WgXcQ")

    def test_malformed_url(self):
        with pytest.raises(UnrecognizedTrackLinkError):
            parse_track_link("not a url at all")

    def test_empty_string(self):
        with pytest.raises(UnrecognizedTrackLinkError):
            parse_track_link("")

    def test_non_http_scheme(self):
        with pytest.raises(UnrecognizedTrackLinkError):
            parse_track_link("ftp://open.spotify.com/track/6y0igZArWVi6Iz0rj35c1Y")


class TestFetchTrackById:
    @pytest.mark.asyncio
    async def test_dispatches_to_spotify_adapter(self, monkeypatch):
        expected = TrackResult(
            source="spotify", source_id="abc123", title="T", artist="A"
        )

        async def fake_fetch_by_id(self, track_id):
            assert track_id == "abc123"
            return expected

        monkeypatch.setattr(SpotifySearchSource, "fetch_by_id", fake_fetch_by_id)
        track = await fetch_track_by_id(
            "spotify", "abc123", spotify_client_id="id", spotify_client_secret="secret"
        )
        assert track is expected

    @pytest.mark.asyncio
    async def test_dispatches_to_apple_adapter(self, monkeypatch):
        expected = TrackResult(source="apple", source_id="999", title="T", artist="A")

        async def fake_fetch_by_id(self, track_id):
            assert track_id == "999"
            return expected

        monkeypatch.setattr(AppleSearchSource, "fetch_by_id", fake_fetch_by_id)
        track = await fetch_track_by_id(
            "apple", "999", spotify_client_id=None, spotify_client_secret=None
        )
        assert track is expected

    @pytest.mark.asyncio
    async def test_spotify_not_configured_raises_specific_error(self):
        with pytest.raises(TrackLinkSourceNotConfiguredError) as exc_info:
            await fetch_track_by_id(
                "spotify", "abc123", spotify_client_id=None, spotify_client_secret=None
            )
        assert "Spotify" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_spotify_not_found_raises_specific_error(self, monkeypatch):
        async def fake_fetch_by_id(self, track_id):
            return None

        monkeypatch.setattr(SpotifySearchSource, "fetch_by_id", fake_fetch_by_id)
        with pytest.raises(TrackLinkNotFoundError) as exc_info:
            await fetch_track_by_id(
                "spotify",
                "abc123",
                spotify_client_id="id",
                spotify_client_secret="secret",
            )
        assert "Spotify" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_apple_not_found_raises_specific_error(self, monkeypatch):
        async def fake_fetch_by_id(self, track_id):
            return None

        monkeypatch.setattr(AppleSearchSource, "fetch_by_id", fake_fetch_by_id)
        with pytest.raises(TrackLinkNotFoundError) as exc_info:
            await fetch_track_by_id(
                "apple", "999", spotify_client_id=None, spotify_client_secret=None
            )
        assert "Apple Music" in str(exc_info.value)
