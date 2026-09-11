"""Apple/iTunes Search API adapter.

Keyless, no auth required. Historically decent coverage of regional (Indian film/devotional)
catalogs, so this is the primary source per the mission's operator context. Docs:
https://performance-partners.apple.com/search-api
"""

from __future__ import annotations

import httpx

from app.core.safe_http import POLICY_PUBLIC_ONLY, SafeHttpClient, SafeHttpError
from app.schemas.search import TrackResult
from app.search_sources.base import SearchSource

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
# The Lookup API (a separate endpoint from Search) fetches one item directly by its numeric
# Apple/iTunes id, e.g. what a music.apple.com track link's URL already contains, rather than
# by keyword: https://performance-partners.apple.com/lookup
ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"


class AppleSearchSource(SearchSource):
    name = "apple"

    def __init__(self, client: SafeHttpClient | None = None) -> None:
        self._client = client or SafeHttpClient()

    def _normalize(self, item: dict) -> TrackResult | None:
        """Maps one iTunes API song entry to TrackResult. Shared by both Search and Lookup,
        whose "song" entries have the identical shape. Returns None for an entry missing a
        trackId (an occasional malformed/partial upstream entry the free-text path already
        skipped silently; a by-id caller treats None as "not found").
        """
        track_id = item.get("trackId")
        if track_id is None:
            return None
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
        return TrackResult(
            source=self.name,
            source_id=str(track_id),
            title=item.get("trackName") or "Unknown title",
            artist=item.get("artistName") or "Unknown artist",
            album=item.get("collectionName"),
            duration_seconds=(duration_ms // 1000) if duration_ms else None,
            artwork_url=artwork,
            year=year,
        )

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
            track = self._normalize(item)
            if track is not None:
                results.append(track)
        return results

    async def fetch_by_id(self, track_id: str) -> TrackResult | None:
        """Resolves one exact track by its Apple/iTunes numeric id (the id embedded in a pasted
        music.apple.com track link), for POST /api/search/resolve-link. Returns None if Apple
        has no song with that id (deleted, region-locked, or the id belonged to a non-song
        item); never raises for that case, only for a genuine transport/HTTP failure. Unlike
        `_search()` (whose failures are caught and normalized by the base class's `run()`),
        nothing wraps this method, so an HTTP-status failure is converted to SafeHttpError here
        rather than left as a raw httpx.HTTPStatusError — the caller (app.services.link_resolve)
        only needs to handle the one typed exception either way.
        """
        try:
            resp = await self._client.get(
                ITUNES_LOOKUP_URL,
                policy=POLICY_PUBLIC_ONLY,
                params={"id": track_id, "entity": "song"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise SafeHttpError(
                f"Apple lookup failed for id {track_id!r}: {exc}"
            ) from exc
        except ValueError as exc:
            raise SafeHttpError(
                f"Apple lookup returned malformed JSON for id {track_id!r}"
            ) from exc
        for item in data.get("results", []):
            track = self._normalize(item)
            if track is not None:
                return track
        return None
