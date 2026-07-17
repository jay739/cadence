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

    async def is_complete(self, torrent_hash: str) -> bool:
        if not self.configured or self._sid_cookie is None:
            return False
        resp = await self._http.get(
            f"{self._base_url}/api/v2/torrents/info",
            policy=POLICY_ALLOW_PRIVATE,
            params={"hashes": torrent_hash},
            headers={"Cookie": f"SID={self._sid_cookie}"},
        )
        if resp.status_code != 200:
            return False
        torrents = resp.json()
        if not torrents:
            return False
        return torrents[0].get("progress", 0) >= 1.0

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
