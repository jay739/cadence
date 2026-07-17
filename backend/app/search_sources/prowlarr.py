"""Prowlarr search adapter.

Searches the operator's own configured indexers via Prowlarr's `/api/v1/search` endpoint.
Results are raw releases (filename-derived), not canonical metadata, and are shown as their
own tab so duplicates across sources stay distinguishable via the source badge in the UI.

Also used, with a track-specific query, by the fulfillment worker (see
app.services.fulfillment) as the first attempt for satisfying an approved request.
"""

from __future__ import annotations

from app.core.safe_http import POLICY_ALLOW_PRIVATE, SafeHttpClient
from app.schemas.search import TrackResult
from app.search_sources.base import SearchSource

# Prowlarr's "audio" search category (Sonarr/Radarr/Prowlarr's shared category numbering).
AUDIO_CATEGORY_IDS = [3000, 3010, 3020, 3030, 3040]

# Prowlarr's /api/v1/search fans a single call out across every configured indexer, so it is
# routinely much slower than the other sources' single-call APIs (observed ~20s against a real
# instance with a handful of indexers configured). The shared SafeHttpClient default (8s, tuned
# for Apple/MusicBrainz/Spotify's single-call APIs) is far too short here and would make every
# real Prowlarr search time out.
PROWLARR_TIMEOUT_SECONDS = 45.0


class ProwlarrSearchSource(SearchSource):
    name = "prowlarr"

    def __init__(
        self,
        base_url: str | None,
        api_key: str | None,
        http: SafeHttpClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") if base_url else None
        self._api_key = api_key
        self._http = http or SafeHttpClient(
            timeout_seconds=PROWLARR_TIMEOUT_SECONDS, max_retries=0
        )

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._api_key)

    async def search_raw(self, query: str, *, limit: int = 50) -> list[dict]:
        """Return raw Prowlarr release dicts (used by both search tab and fulfillment)."""
        if not self.configured:
            return []
        resp = await self._http.get(
            f"{self._base_url}/api/v1/search",
            policy=POLICY_ALLOW_PRIVATE,
            params={
                "query": query,
                "categories": AUDIO_CATEGORY_IDS,
                "type": "search",
                "limit": str(limit),
            },
            headers={"X-Api-Key": self._api_key},
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    async def _search(self, query: str, *, limit: int = 20) -> list[TrackResult]:
        raw = await self.search_raw(query, limit=limit)
        results: list[TrackResult] = []
        for item in raw:
            guid = item.get("guid")
            if not guid:
                continue
            results.append(
                TrackResult(
                    source=self.name,
                    source_id=guid,
                    title=item.get("title") or "Unknown release",
                    artist=item.get("artist") or "",
                    album=item.get("album"),
                    indexer=item.get("indexer"),
                    size_bytes=item.get("size"),
                    seeders=item.get("seeders"),
                    download_url=item.get("downloadUrl") or item.get("magnetUrl"),
                    guid=guid,
                )
            )
        return results

    async def run(self, query: str, *, limit: int = 20):
        if not self.configured:
            return await self.not_configured()
        return await super().run(query, limit=limit)

    async def grab(self, guid: str, indexer_id: int) -> bool:
        """Trigger Prowlarr's grab API for a chosen release. Returns True on HTTP success."""
        if not self.configured:
            return False
        resp = await self._http.post(
            f"{self._base_url}/api/v1/search",
            policy=POLICY_ALLOW_PRIVATE,
            json={"guid": guid, "indexerId": indexer_id},
            headers={"X-Api-Key": self._api_key},
        )
        return resp.status_code < 300
