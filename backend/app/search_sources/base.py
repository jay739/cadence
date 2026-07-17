"""Shared contract for a search source adapter.

Every adapter's `search()` must never raise: it always returns a SourceSearchResult, catching
and recording its own errors, so a fan-out over all sources cannot have one slow/dead source
take down the rest. Timeouts/HTTP errors are caught in the base `run()` wrapper as a safety
net even if an individual adapter forgets to.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

import structlog

from app.core.safe_http import SafeHttpError
from app.schemas.search import SourceSearchResult, TrackResult

logger = structlog.get_logger(__name__)


class SearchSource(ABC):
    name: str

    @abstractmethod
    async def _search(self, query: str, *, limit: int) -> list[TrackResult]:
        """Perform the actual search. May raise; `run()` catches and normalizes."""

    async def run(self, query: str, *, limit: int = 20) -> SourceSearchResult:
        start = time.monotonic()
        try:
            results = await self._search(query, limit=limit)
            return SourceSearchResult(source=self.name, ok=True, results=results)
        except SafeHttpError as exc:
            logger.warning(
                "search_source_blocked_or_failed", source=self.name, error=str(exc)
            )
            return SourceSearchResult(
                source=self.name, ok=False, error=str(exc), results=[]
            )
        except (
            Exception
        ) as exc:  # noqa: BLE001 - deliberate: isolate any adapter failure
            logger.error(
                "search_source_unexpected_error",
                source=self.name,
                error=str(exc),
                exc_info=True,
            )
            return SourceSearchResult(
                source=self.name,
                ok=False,
                error="Unexpected error, see server logs",
                results=[],
            )
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.info(
                "search_source_timing",
                source=self.name,
                elapsed_ms=round(elapsed_ms, 1),
            )

    async def not_configured(self) -> SourceSearchResult:
        return SourceSearchResult(
            source=self.name, ok=False, error="Not configured", results=[]
        )
