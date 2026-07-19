from __future__ import annotations

import re

import pytest

from app.core.config import Settings
from app.services.search import multi_source_search

pytestmark = pytest.mark.asyncio


class TestMultiSourceSearchDemoMode:
    async def test_demo_mode_returns_four_sources_without_network(self):
        settings = Settings(DEMO_MODE=True, SESSION_SECRET="x" * 40)
        result = await multi_source_search("Tum Hi Ho", settings)
        assert {s.source for s in result.sources} == {
            "apple",
            "musicbrainz",
            "spotify",
            "prowlarr",
        }
        assert all(s.ok for s in result.sources)

    async def test_empty_query_short_circuits(self):
        settings = Settings(DEMO_MODE=True, SESSION_SECRET="x" * 40)
        result = await multi_source_search("   ", settings)
        assert result.sources == []


class TestMultiSourceSearchLiveModeIsolation:
    async def test_no_sources_configured_all_report_not_configured_or_public(
        self, httpx_mock
    ):
        """With no Spotify/Prowlarr config, those two report 'Not configured'; Apple and
        MusicBrainz are keyless and will attempt a real call, so we mock them to keep this
        test network-free."""
        settings = Settings(DEMO_MODE=False, SESSION_SECRET="x" * 40)

        httpx_mock.add_response(
            url=re.compile(r"https://itunes\.apple\.com/.*"), json={"results": []}
        )
        httpx_mock.add_response(
            url=re.compile(r"https://musicbrainz\.org/.*"), json={"recordings": []}
        )

        result = await multi_source_search("test query", settings)
        by_source = {s.source: s for s in result.sources}
        assert by_source["spotify"].ok is False
        assert by_source["spotify"].error == "Not configured"
        assert by_source["prowlarr"].ok is False
        assert by_source["prowlarr"].error == "Not configured"
        # Apple/MusicBrainz still attempted and succeeded (empty results, but ok).
        assert by_source["apple"].ok is True
        assert by_source["musicbrainz"].ok is True

    async def test_one_source_failing_does_not_block_others(self, httpx_mock):
        settings = Settings(DEMO_MODE=False, SESSION_SECRET="x" * 40)

        httpx_mock.add_response(
            url=re.compile(r"https://itunes\.apple\.com/.*"), status_code=500
        )
        httpx_mock.add_response(
            url=re.compile(r"https://musicbrainz\.org/.*"), json={"recordings": []}
        )

        result = await multi_source_search("test query", settings)
        by_source = {s.source: s for s in result.sources}
        assert by_source["apple"].ok is False
        assert by_source["musicbrainz"].ok is True


class TestStreamCancellation:
    async def test_closing_the_stream_early_cancels_still_pending_sources(
        self, monkeypatch
    ):
        """With search-as-you-type, every keystroke abandons the previous stream; the
        still-pending source tasks must be cancelled on generator close, not left running to
        completion in the background (where MusicBrainz's process-wide 1-req/sec limiter would
        keep serving stale queries ahead of the live one).
        """
        import asyncio

        from app.schemas.search import TrackResult
        from app.search_sources.base import SearchSource
        from app.services import search as search_module
        from app.services.search import stream_source_results

        class InstantSource(SearchSource):
            name = "instant"

            async def _search(self, query: str, *, limit: int) -> list[TrackResult]:
                return []

        class HangingSource(SearchSource):
            name = "hanging"
            cancelled = False

            async def _search(self, query: str, *, limit: int) -> list[TrackResult]:
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    type(self).cancelled = True
                    raise
                return []

        hanging = HangingSource()

        async def fake_resolved_sources(settings, session):
            return settings, [InstantSource(), hanging]

        monkeypatch.setattr(search_module, "_resolved_sources", fake_resolved_sources)

        settings = Settings(DEMO_MODE=False, SESSION_SECRET="x" * 40)
        stream = stream_source_results("test query", settings, None)

        first = await stream.__anext__()
        assert first.source == "instant"

        # Client walks away after the first event; aclose() drives the generator's finally
        # block, which must cancel (and reap) the hanging task before returning.
        await stream.aclose()
        assert HangingSource.cancelled is True
