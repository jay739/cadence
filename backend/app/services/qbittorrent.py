"""Minimal qBittorrent Web API client used to poll grabbed-release completion.

Prowlarr hands a grabbed release off to qBittorrent directly (it is configured as Prowlarr's
download client in the operator's stack), so Cadence's job here is only to poll qBittorrent
for the matching torrent's completion state and locate the resulting file, not to submit the
download itself.
"""

from __future__ import annotations

import structlog

from app.core.safe_http import POLICY_ALLOW_PRIVATE, SafeHttpClient

logger = structlog.get_logger(__name__)


class QBittorrentClient:
    def __init__(
        self,
        base_url: str | None,
        username: str | None,
        password: str | None,
        http: SafeHttpClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") if base_url else None
        self._username = username
        self._password = password
        self._http = http or SafeHttpClient()
        self._sid_cookie: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._username and self._password)

    async def _login(self) -> bool:
        if not self.configured:
            return False
        resp = await self._http.post(
            f"{self._base_url}/api/v2/auth/login",
            policy=POLICY_ALLOW_PRIVATE,
            data={"username": self._username, "password": self._password},
        )
        if resp.status_code != 200 or resp.text.strip() != "Ok.":
            logger.warning("qbittorrent_login_failed", status=resp.status_code)
            return False
        self._sid_cookie = resp.cookies.get("SID")
        return True

    async def find_torrent_by_hint(self, name_hint: str) -> dict | None:
        """Best-effort lookup of a torrent whose name contains `name_hint`."""
        if not self.configured:
            return None
        if self._sid_cookie is None and not await self._login():
            return None
        resp = await self._http.get(
            f"{self._base_url}/api/v2/torrents/info",
            policy=POLICY_ALLOW_PRIVATE,
            headers={"Cookie": f"SID={self._sid_cookie}"},
        )
        if resp.status_code != 200:
            return None
        torrents = resp.json()
        hint_lower = name_hint.lower()
        for t in torrents:
            if hint_lower in (t.get("name") or "").lower():
                return t
        return None

    async def _torrent_info(self, torrent_hash: str) -> dict | None:
        """The single torrent matching `torrent_hash`, or None if it cannot be read.

        Shared by `is_complete` and `progress_percent` so that one poll tick asking both
        questions is still one HTTP call's worth of logic rather than two divergent copies.
        """
        if not self.configured or self._sid_cookie is None:
            return None
        resp = await self._http.get(
            f"{self._base_url}/api/v2/torrents/info",
            policy=POLICY_ALLOW_PRIVATE,
            params={"hashes": torrent_hash},
            headers={"Cookie": f"SID={self._sid_cookie}"},
        )
        if resp.status_code != 200:
            return None
        torrents = resp.json()
        if not torrents:
            return None
        first = torrents[0]
        return first if isinstance(first, dict) else None

    async def is_complete(self, torrent_hash: str) -> bool:
        torrent = await self._torrent_info(torrent_hash)
        if torrent is None:
            return False
        return torrent.get("progress", 0) >= 1.0

    async def progress_percent(self, torrent_hash: str) -> float | None:
        """How far along this torrent is, 0..100, or None if qBittorrent did not say.

        qBittorrent is the one fulfillment tier that reports a genuine fraction: `progress` is
        a 0..1 float on the same torrents/info payload `is_complete` already reads, so showing
        a percentage here costs no extra call and invents nothing. None (rather than 0) when
        the torrent cannot be read or the field is missing/unusable, because "we don't know
        yet" and "no bytes have arrived" are different things to show a requester.
        """
        torrent = await self._torrent_info(torrent_hash)
        if torrent is None:
            return None
        raw = torrent.get("progress")
        if not isinstance(raw, int | float) or isinstance(raw, bool):
            return None
        return max(0.0, min(100.0, float(raw) * 100.0))

    async def get_content_path(self, torrent_hash: str) -> str | None:
        if not self.configured or self._sid_cookie is None:
            return None
        resp = await self._http.get(
            f"{self._base_url}/api/v2/torrents/files",
            policy=POLICY_ALLOW_PRIVATE,
            params={"hash": torrent_hash},
            headers={"Cookie": f"SID={self._sid_cookie}"},
        )
        if resp.status_code != 200:
            return None
        files = resp.json()
        if not files:
            return None
        # Pick the largest file in the torrent (the audio file, vs. any nfo/cover clutter).
        largest = max(files, key=lambda f: f.get("size", 0))
        return largest.get("name")
