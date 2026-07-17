"""Multi-source search fan-out.

Runs every configured source in parallel via asyncio.gather(..., return_exceptions=True) as a
final safety net (each adapter already catches its own errors in SearchSource.run(), but this
outer gather ensures that even a bug in an adapter cannot take down the whole request).
"""

from __future__ import annotations

import asyncio

import structlog

from app.core.config import Settings
from app.demo.data import demo_search_results
from app.schemas.search import MultiSearchResponse, SourceSearchResult
from app.search_sources.apple import AppleSearchSource
from app.search_sources.musicbrainz import MusicBrainzSearchSource
from app.search_sources.prowlarr import ProwlarrSearchSource
from app.search_sources.spotify import SpotifySearchSource

logger = structlog.get_logger(__name__)


async def multi_source_search(
    query: str, settings: Settings, *, limit: int = 20
) -> MultiSearchResponse:
    query = query.strip()
    if not query:
        return MultiSearchResponse(query=query, sources=[])

    if settings.demo_mode:
        return MultiSearchResponse(query=query, sources=demo_search_results(query))

    sources = [
        AppleSearchSource(),
        MusicBrainzSearchSource(),
        SpotifySearchSource(settings.spotify_client_id, settings.spotify_client_secret),
        ProwlarrSearchSource(settings.prowlarr_url, settings.prowlarr_api_key),
    ]

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
