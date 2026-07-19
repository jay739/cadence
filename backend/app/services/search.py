"""Multi-source search fan-out.

Runs every configured source in parallel via asyncio.gather(..., return_exceptions=True) as a
final safety net (each adapter already catches its own errors in SearchSource.run(), but this
outer gather ensures that even a bug in an adapter cannot take down the whole request).

`stream_source_results()` below runs the exact same sources but yields each one's
SourceSearchResult as soon as that source finishes, instead of waiting for the slowest of the
four. Apple and Spotify's Client Credentials token+search usually resolve in well under a
second; MusicBrainz is hard rate-limited to 1 request/second by their own usage policy, and
Prowlarr can take several seconds against a slow or misconfigured indexer set. Waiting for all
four via `multi_source_search()` means the whole response is only as fast as the slowest one;
streaming lets the UI show the three fast tabs immediately while the slow one is still loading.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.demo.data import demo_search_results
from app.schemas.search import MultiSearchResponse, SourceSearchResult
from app.search_sources.apple import AppleSearchSource
from app.search_sources.base import SearchSource
from app.search_sources.musicbrainz import MusicBrainzSearchSource
from app.search_sources.prowlarr import ProwlarrSearchSource
from app.search_sources.spotify import SpotifySearchSource
from app.services.settings_store import apply_effective_settings

logger = structlog.get_logger(__name__)


async def _resolved_sources(
    settings: Settings, session: AsyncSession | None
) -> tuple[Settings, list[SearchSource]]:
    # `session` is optional so unit tests exercising this function directly against a bare
    # Settings object (no DB fixture) keep working unchanged; the live route always passes one
    # so the mission-scoped fields (Prowlarr/Spotify/etc.) reflect DB-saved admin settings too,
    # not just env vars, without a restart.
    if session is not None:
        settings = await apply_effective_settings(session, settings)

    sources: list[SearchSource] = [
        AppleSearchSource(),
        MusicBrainzSearchSource(),
        SpotifySearchSource(settings.spotify_client_id, settings.spotify_client_secret),
        ProwlarrSearchSource(settings.prowlarr_url, settings.prowlarr_api_key),
    ]
    return settings, sources


async def multi_source_search(
    query: str,
    settings: Settings,
    session: AsyncSession | None = None,
    *,
    limit: int = 20,
) -> MultiSearchResponse:
    query = query.strip()
    if not query:
        return MultiSearchResponse(query=query, sources=[])

    if settings.demo_mode:
        return MultiSearchResponse(query=query, sources=demo_search_results(query))

    settings, sources = await _resolved_sources(settings, session)

    tasks = [source.run(query, limit=limit) for source in sources]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    normalized: list[SourceSearchResult] = []
    for source, result in zip(sources, results, strict=True):
        if isinstance(result, BaseException):
            logger.error(
                "search_source_gather_exception",
                source=source.name,
                error=str(result),
                exc_info=result,
            )
            normalized.append(
                SourceSearchResult(
                    source=source.name, ok=False, error="Internal error", results=[]
                )
            )
        else:
            normalized.append(result)

    return MultiSearchResponse(query=query, sources=normalized)


async def _run_source_safely(
    source: SearchSource, query: str, limit: int
) -> SourceSearchResult:
    """Wraps source.run() so even an exception that somehow escapes it still identifies which
    source failed, keeping that source's tab from silently never hearing back from the stream
    (SearchSource.run() already catches everything a well-behaved adapter can raise, so this is
    a last-resort safety net, mirroring multi_source_search's gather(return_exceptions=True)).
    """
    try:
        return await source.run(query, limit=limit)
    except Exception as exc:  # noqa: BLE001 - deliberate, see docstring
        logger.error(
            "search_source_stream_exception",
            source=source.name,
            error=str(exc),
            exc_info=True,
        )
        return SourceSearchResult(
            source=source.name, ok=False, error="Internal error", results=[]
        )


async def stream_source_results(
    query: str,
    settings: Settings,
    session: AsyncSession | None = None,
    *,
    limit: int = 20,
) -> AsyncIterator[SourceSearchResult]:
    """Yield each source's SourceSearchResult as soon as that source finishes.

    Demo mode still returns everything at once (seeded data has no real latency to stream
    around); the ordering of yields for real sources depends on which finishes first, not the
    fixed order used by multi_source_search's response, since that is the entire point.
    """
    query = query.strip()
    if not query:
        return

    if settings.demo_mode:
        for source_result in demo_search_results(query):
            yield source_result
        return

    settings, sources = await _resolved_sources(settings, session)

    tasks = [
        asyncio.ensure_future(_run_source_safely(source, query, limit))
        for source in sources
    ]
    try:
        for coro in asyncio.as_completed(tasks):
            yield await coro
    finally:
        # A client abandoning the stream mid-flight (with search-as-you-type, every keystroke
        # abandons the previous stream) closes this generator at the suspended yield; without
        # cancelling here, the still-pending source tasks would run to completion in the
        # background, and in MusicBrainz's case keep consuming its process-wide 1-req/sec
        # budget on a query nobody is waiting for anymore, delaying the query they ARE
        # waiting for. CancelledError passes through SearchSource.run()'s broad `except
        # Exception` untouched (it is a BaseException), so cancellation lands cleanly.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
