"""Client for the ytdlp-shim service: search YouTube, download a video's audio, poll for it.

The last fulfillment tier, tried only after Prowlarr and Soulseek both come up empty. YouTube
is a fallback specifically because torrent/Soulseek coverage skews thin on regional and
long-tail catalog, exactly where this gap matters most, but it is the least reliable source
(dependent on yt-dlp's extractor staying ahead of YouTube's own changes) and never treated as
authoritative metadata, only as an audio source scored the same way as every other candidate.

Talks to the ytdlp-shim service over its own HTTP API (see ytdlp-shim/app/main.py), not to
yt-dlp directly: that process isolation is deliberate, so an extractor break there degrades to
this one tier failing, not to Cadence's own process.
"""

from __future__ import annotations

import structlog

from app.core.safe_http import POLICY_ALLOW_PRIVATE, SafeHttpClient

logger = structlog.get_logger(__name__)


class YtdlpShimClient:
    def __init__(
        self, base_url: str | None, http: SafeHttpClient | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/") if base_url else None
        self._http = http or SafeHttpClient()

    @property
    def configured(self) -> bool:
        return bool(self._base_url)

    async def search(self, query: str, *, limit: int = 10) -> list[dict]:
        if not self.configured:
            return []
        resp = await self._http.post(
            f"{self._base_url}/search",
            policy=POLICY_ALLOW_PRIVATE,
            json={"query": query, "limit": limit},
        )
        if resp.status_code != 200:
            logger.warning("ytdlp_shim_search_failed", status=resp.status_code)
            return []
        return resp.json()

    async def start_download(self, video_id: str) -> str | None:
        """Start a download job and return its id, or None if the shim rejected the request."""
        if not self.configured:
            return None
        resp = await self._http.post(
            f"{self._base_url}/downloads",
            policy=POLICY_ALLOW_PRIVATE,
            json={"video_id": video_id},
        )
        if resp.status_code != 202:
            return None
        return resp.json().get("job_id")

    async def get_download_status(
        self, job_id: str
    ) -> tuple[str, str | None, str | None]:
        """Check a download job's current state once. Returns (state, filename, error).

        A single check, deliberately: the caller (fulfillment.py) owns the bounded wait/poll
        loop shared with the slskd tier, rather than each client inventing its own waiting
        policy. state is "unknown" if the shim itself could not be reached for this check,
        distinct from the shim's own "failed", so a transient blip does not read the same as
        yt-dlp genuinely giving up.
        """
        if not self.configured:
            return ("unknown", None, "ytdlp-shim not configured")
        resp = await self._http.get(
            f"{self._base_url}/downloads/{job_id}", policy=POLICY_ALLOW_PRIVATE
        )
        if resp.status_code != 200:
            return ("unknown", None, None)
        body = resp.json()
        return (body.get("state", "unknown"), body.get("filename"), body.get("error"))
