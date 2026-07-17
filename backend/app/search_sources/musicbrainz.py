"""MusicBrainz recording search adapter.

Keyless but rate-limited to 1 request/second per MusicBrainz's usage policy
(https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting). Used both as a search tab and,
once a track is picked, as the canonical-metadata source for tagging. A process-wide async
limiter enforces the 1 req/sec ceiling regardless of how many concurrent searches are in
flight, since MusicBrainz bans by IP for violations.
"""

from __future__ import annotations

import asyncio
import re
import time

from app.core.safe_http import POLICY_PUBLIC_ONLY, SafeHttpClient
from app.schemas.search import TrackResult
from app.search_sources.base import SearchSource

MUSICBRAINZ_SEARCH_URL = "https://musicbrainz.org/ws/2/recording"
USER_AGENT = "Cadence/0.1.0 (self-hosted single-track music request service)"

# MBIDs are always UUIDs. Enforced again here, independent of Pydantic-level validation
# upstream, since this function raw-interpolates its argument into a URL path and must never
# trust a caller blindly (defense in depth: a future call site might not go through the
# CreateRequestIn schema's validator at all).
_MBID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


class _RateLimiter:
    """Simple process-wide 1-req/sec limiter, shared by all MusicBrainz calls."""

    def __init__(self, min_interval_seconds: float = 1.0) -> None:
        self._min_interval = min_interval_seconds
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_call = time.monotonic()


_rate_limiter = _RateLimiter()


class MusicBrainzSearchSource(SearchSource):
    name = "musicbrainz"

    def __init__(self, client: SafeHttpClient | None = None) -> None:
        self._client = client or SafeHttpClient()

    async def _search(self, query: str, *, limit: int = 20) -> list[TrackResult]:
        await _rate_limiter.wait()
        resp = await self._client.get(
            MUSICBRAINZ_SEARCH_URL,
            policy=POLICY_PUBLIC_ONLY,
            params={
                "query": f'recording:"{query}"',
                "fmt": "json",
                "limit": str(min(limit, 50)),
            },
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()
        results: list[TrackResult] = []
        for rec in data.get("recordings", []):
            artist_credit = rec.get("artist-credit") or []
            artist_name = (
                artist_credit[0]["artist"]["name"]
                if artist_credit
                else "Unknown artist"
            )
            releases = rec.get("releases") or []
            album = releases[0].get("title") if releases else None
            year = None
            if releases:
                date_str = releases[0].get("date", "")
                if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
                    year = int(date_str[:4])
            duration_ms = rec.get("length")
            results.append(
                TrackResult(
                    source=self.name,
                    source_id=rec["id"],
                    title=rec.get("title") or "Unknown title",
                    artist=artist_name,
                    album=album,
                    duration_seconds=(duration_ms // 1000) if duration_ms else None,
                    year=year,
                    musicbrainz_recording_id=rec["id"],
                )
            )
        return results


async def lookup_recording(
    recording_id: str, client: SafeHttpClient | None = None
) -> dict | None:
    """Fetch canonical tag data for a single recording, used by the tagging step."""
    if not _MBID_RE.match(recording_id):
        return None
    http = client or SafeHttpClient()
    await _rate_limiter.wait()
    resp = await http.get(
        f"{MUSICBRAINZ_SEARCH_URL}/{recording_id}",
        policy=POLICY_PUBLIC_ONLY,
        params={"fmt": "json", "inc": "artist-credits+releases"},
        headers={"User-Agent": USER_AGENT},
    )
    if resp.status_code != 200:
        return None
    return resp.json()
