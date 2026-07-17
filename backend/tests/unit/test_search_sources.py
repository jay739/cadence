"""Unit tests for per-source response normalization into the common TrackResult shape.

Each source's real API returns a very different JSON shape; these tests assert the adapters
map them all into one internal model correctly, and that adapters never raise even on
malformed/error responses (SearchSource.run always returns a SourceSearchResult).
"""

from __future__ import annotations

import httpx
import pytest

from app.search_sources.apple import AppleSearchSource
from app.search_sources.musicbrainz import MusicBrainzSearchSource, _rate_limiter
from app.search_sources.prowlarr import ProwlarrSearchSource
from app.search_sources.spotify import SpotifySearchSource


@pytest.fixture(autouse=True)
def _reset_musicbrainz_rate_limiter():
    """Avoid the 1req/sec limiter making every MusicBrainz test slow/serial."""
    _rate_limiter._last_call = 0.0
    yield


class TestAppleSearchSource:
    @pytest.mark.asyncio
    async def test_normalizes_happy_path(self, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(
                "https://itunes.apple.com/search",
                params={
                    "term": "test",
                    "media": "music",
                    "entity": "song",
                    "limit": "20",
                },
            ),
            json={
                "results": [
                    {
                        "trackId": 123,
                        "trackName": "Tum Hi Ho",
                        "artistName": "Arijit Singh",
                        "collectionName": "Aashiqui 2",
                        "trackTimeMillis": 262000,
                        "artworkUrl100": "https://example.test/100x100bb.jpg",
                        "releaseDate": "2013-04-26T00:00:00Z",
                    }
                ]
            },
        )
        source = AppleSearchSource()
        result = await source.run("test")
        assert result.ok is True
        assert len(result.results) == 1
        track = result.results[0]
        assert track.source == "apple"
        assert track.title == "Tum Hi Ho"
        assert track.artist == "Arijit Singh"
        assert track.duration_seconds == 262
        assert track.year == 2013
        assert "512x512bb" in track.artwork_url

    @pytest.mark.asyncio
    async def test_skips_entries_missing_track_id(self, httpx_mock):
        httpx_mock.add_response(json={"results": [{"trackName": "No ID"}]})
        result = await AppleSearchSource().run("test")
        assert result.ok is True
        assert result.results == []

    @pytest.mark.asyncio
    async def test_handles_malformed_json(self, httpx_mock):
        httpx_mock.add_response(
            content=b"not json", headers={"Content-Type": "application/json"}
        )
        result = await AppleSearchSource().run("test")
        assert result.ok is False
        assert result.results == []

    @pytest.mark.asyncio
    async def test_handles_http_error_status(self, httpx_mock):
        httpx_mock.add_response(status_code=500)
        result = await AppleSearchSource().run("test")
        assert result.ok is False

    @pytest.mark.asyncio
    async def test_handles_timeout(self, httpx_mock):
        # SafeHttpClient retries timeouts (default max_retries=2), so the mock must be able to
        # answer every attempt, not just the first.
        httpx_mock.add_exception(httpx.TimeoutException("timed out"), is_reusable=True)
        result = await AppleSearchSource().run("test")
        assert result.ok is False
        assert result.results == []


class TestMusicBrainzSearchSource:
    @pytest.mark.asyncio
    async def test_normalizes_happy_path(self, httpx_mock):
        httpx_mock.add_response(
            json={
                "recordings": [
                    {
                        "id": "abc-123",
                        "title": "Tum Hi Ho",
                        "length": 262000,
                        "artist-credit": [{"artist": {"name": "Arijit Singh"}}],
                        "releases": [{"title": "Aashiqui 2", "date": "2013-04-26"}],
                    }
                ]
            }
        )
        result = await MusicBrainzSearchSource().run("test")
        assert result.ok is True
        track = result.results[0]
        assert track.musicbrainz_recording_id == "abc-123"
        assert track.artist == "Arijit Singh"
        assert track.year == 2013

    @pytest.mark.asyncio
    async def test_handles_empty_results(self, httpx_mock):
        httpx_mock.add_response(json={"recordings": []})
        result = await MusicBrainzSearchSource().run("test")
        assert result.ok is True
        assert result.results == []

    @pytest.mark.asyncio
    async def test_handles_missing_artist_credit(self, httpx_mock):
        httpx_mock.add_response(json={"recordings": [{"id": "x", "title": "T"}]})
        result = await MusicBrainzSearchSource().run("test")
        assert result.results[0].artist == "Unknown artist"


class TestSpotifySearchSource:
    @pytest.mark.asyncio
    async def test_not_configured_returns_ok_false(self):
        source = SpotifySearchSource(None, None)
        result = await source.run("test")
        assert result.ok is False
        assert result.error == "Not configured"

    @pytest.mark.asyncio
    async def test_normalizes_happy_path(self, httpx_mock):
        httpx_mock.add_response(
            url="https://accounts.spotify.com/api/token",
            json={"access_token": "tok123", "expires_in": 3600},
        )
        httpx_mock.add_response(
            url=httpx.URL(
                "https://api.spotify.com/v1/search",
                params={"q": "test", "type": "track", "limit": "20"},
            ),
            json={
                "tracks": {
                    "items": [
                        {
                            "id": "sp1",
                            "name": "Tum Hi Ho",
                            "artists": [{"name": "Arijit Singh"}],
                            "album": {
                                "name": "Aashiqui 2",
                                "images": [{"url": "https://example.test/art.jpg"}],
                                "release_date": "2013-04-26",
                            },
                            "duration_ms": 262000,
                        }
                    ]
                }
            },
        )
        source = SpotifySearchSource("client-id", "client-secret")
        result = await source.run("test")
        assert result.ok is True
        track = result.results[0]
        assert track.title == "Tum Hi Ho"
        assert track.artwork_url == "https://example.test/art.jpg"

    @pytest.mark.asyncio
    async def test_token_error_isolated_as_source_failure(self, httpx_mock):
        httpx_mock.add_response(
            url="https://accounts.spotify.com/api/token", status_code=401
        )
        source = SpotifySearchSource("bad-id", "bad-secret")
        result = await source.run("test")
        assert result.ok is False


class TestProwlarrSearchSource:
    @pytest.mark.asyncio
    async def test_not_configured_returns_ok_false(self):
        source = ProwlarrSearchSource(None, None)
        result = await source.run("test")
        assert result.ok is False

    @pytest.mark.asyncio
    async def test_normalizes_happy_path(self, httpx_mock):
        httpx_mock.add_response(
            json=[
                {
                    "guid": "release-guid-1",
                    "title": "Arijit Singh - Tum Hi Ho [FLAC]",
                    "indexer": "SomeIndexer",
                    "size": 30_000_000,
                    "seeders": 5,
                    "downloadUrl": "http://localhost:9696/download/1",
                }
            ]
        )
        source = ProwlarrSearchSource("http://localhost:9696", "fake-api-key")
        result = await source.run("test")
        assert result.ok is True
        track = result.results[0]
        assert track.source == "prowlarr"
        assert track.indexer == "SomeIndexer"
        assert track.size_bytes == 30_000_000

    @pytest.mark.asyncio
    async def test_skips_entries_without_guid(self, httpx_mock):
        httpx_mock.add_response(json=[{"title": "no guid here"}])
        source = ProwlarrSearchSource("http://localhost:9696", "fake-api-key")
        result = await source.run("test")
        assert result.results == []

    @pytest.mark.asyncio
    async def test_non_list_response_handled_gracefully(self, httpx_mock):
        httpx_mock.add_response(json={"unexpected": "shape"})
        source = ProwlarrSearchSource("http://localhost:9696", "fake-api-key")
        result = await source.run("test")
        assert result.ok is True
        assert result.results == []
