"""Apple/iTunes Search API adapter.

Keyless, no auth required. Historically decent coverage of regional (Indian film/devotional)
catalogs, so this is the primary source per the mission's operator context. Docs:
https://performance-partners.apple.com/search-api
"""

from __future__ import annotations

from app.core.safe_http import POLICY_PUBLIC_ONLY, SafeHttpClient
from app.schemas.search import TrackResult
from app.search_sources.base import SearchSource

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"


class AppleSearchSource(SearchSource):
    name = "apple"

    def __init__(self, client: SafeHttpClient | None = None) -> None:
        self._client = client or SafeHttpClient()

    async def _search(self, query: str, *, limit: int = 20) -> list[TrackResult]:
        resp = await self._client.get(
            ITUNES_SEARCH_URL,
            policy=POLICY_PUBLIC_ONLY,
            params={
                "term": query,
                "media": "music",
                "entity": "song",
                "limit": str(min(limit, 50)),
            },
        )
        resp.raise_for_status()
        data = resp.json()
        results: list[TrackResult] = []
        for item in data.get("results", []):
            track_id = item.get("trackId")
            if track_id is None:
                continue
            artwork = item.get("artworkUrl100")
            if artwork:
                # Request higher-resolution art than the default 100x100 thumbnail.
                artwork = artwork.replace("100x100bb", "512x512bb")
            duration_ms = item.get("trackTimeMillis")
            release_date = item.get("releaseDate", "")
            year = None
            if (
                isinstance(release_date, str)
                and len(release_date) >= 4
                and release_date[:4].isdigit()
            ):
                year = int(release_date[:4])
            results.append(
                TrackResult(
                    source=self.name,
                    source_id=str(track_id),
                    title=item.get("trackName") or "Unknown title",
                    artist=item.get("artistName") or "Unknown artist",
                    album=item.get("collectionName"),
                    duration_seconds=(duration_ms // 1000) if duration_ms else None,
                    artwork_url=artwork,
                    year=year,
                )
            )
        return results
