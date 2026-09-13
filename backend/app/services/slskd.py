"""slskd (Soulseek daemon) client: search, evaluate, download a single file, poll completion.

Used only as the fallback path when Prowlarr yields nothing usable within the configured
attempt budget. slskd's HTTP API: https://github.com/slskd/slskd/blob/master/docs/api.md
"""

from __future__ import annotations

import asyncio

import structlog

from app.core.safe_http import POLICY_ALLOW_PRIVATE, SafeHttpClient

logger = structlog.get_logger(__name__)


class SlskdClient:
    def __init__(
        self,
        base_url: str | None,
        api_key: str | None,
        http: SafeHttpClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") if base_url else None
        self._api_key = api_key
        self._http = http or SafeHttpClient()

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._api_key)

    def _headers(self) -> dict:
        return {"X-API-Key": self._api_key} if self._api_key else {}

    async def search(self, query: str, *, timeout_seconds: int = 12) -> list[dict]:
        """Start a slskd search, wait briefly for responses, and return matched files.

        slskd's search API is asynchronous: a search is created, then polled for responses.
        """
        if not self.configured:
            return []
        create_resp = await self._http.post(
            f"{self._base_url}/api/v0/searches",
            policy=POLICY_ALLOW_PRIVATE,
            json={"searchText": query},
            headers=self._headers(),
        )
        if create_resp.status_code >= 300:
            logger.warning("slskd_search_create_failed", status=create_resp.status_code)
            return []
        search_id = create_resp.json().get("id")
        if not search_id:
            return []

        deadline = asyncio.get_event_loop().time() + timeout_seconds
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1.5)
            state_resp = await self._http.get(
                f"{self._base_url}/api/v0/searches/{search_id}",
                policy=POLICY_ALLOW_PRIVATE,
                headers=self._headers(),
            )
            if state_resp.status_code != 200:
                continue
            state = state_resp.json()
            if state.get("isComplete"):
                break

        responses_resp = await self._http.get(
            f"{self._base_url}/api/v0/searches/{search_id}/responses",
            policy=POLICY_ALLOW_PRIVATE,
            headers=self._headers(),
        )
        if responses_resp.status_code != 200:
            return []

        files: list[dict] = []
        for response in responses_resp.json():
            username = response.get("username")
            for f in response.get("files", []):
                files.append(
                    {
                        "username": username,
                        "filename": f.get("filename"),
                        "size": f.get("size"),
                        "bitRate": f.get("bitRate"),
                    }
                )
        return files

    async def enqueue_download(
        self, username: str, filename: str, size: int | None
    ) -> bool:
        if not self.configured:
            return False
        resp = await self._http.post(
            f"{self._base_url}/api/v0/transfers/downloads/{username}",
            policy=POLICY_ALLOW_PRIVATE,
            json=[{"filename": filename, "size": size}],
            headers=self._headers(),
        )
        return resp.status_code < 300

    async def _transfer_file(self, username: str, filename: str) -> dict | None:
        """The per-file transfer object slskd holds for this download, or None.

        Shared by `is_download_complete` and `download_progress`: both questions are answered
        by the same object on the same endpoint, so they read it the same way rather than
        drifting apart.
        """
        if not self.configured:
            return None
        resp = await self._http.get(
            f"{self._base_url}/api/v0/transfers/downloads/{username}",
            policy=POLICY_ALLOW_PRIVATE,
            headers=self._headers(),
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        for directory in data.get("directories", []):
            for f in directory.get("files", []):
                if f.get("filename") == filename:
                    return f if isinstance(f, dict) else None
        return None

    async def is_download_complete(self, username: str, filename: str) -> bool:
        transfer = await self._transfer_file(username, filename)
        if transfer is None:
            return False
        # slskd reports composite states like "Completed, Succeeded" or
        # "Completed, Errored" - only the former means the file is usable.
        state = transfer.get("state", "").lower()
        return "completed" in state and "succeeded" in state

    async def download_progress(self, username: str, filename: str) -> float | None:
        """How far along this transfer is, 0..100, or None if slskd did not say.

        Verified against a live slskd 0.26.0.0 rather than assumed: every per-file transfer
        object carries `percentComplete` (already 0..100) alongside `bytesTransferred`,
        `size`, `bytesRemaining` and `averageSpeed`. `percentComplete` is preferred because it
        is slskd's own answer; the byte counts are the fallback for the case where that field
        is absent (an older daemon, or a transfer slskd has not scored yet), so a missing
        field degrades to "no number" rather than to a wrong one.

        None, never 0.0, when nothing usable is available: a transfer that has not started and
        a transfer whose progress cannot be read must not both render as an empty bar.
        """
        transfer = await self._transfer_file(username, filename)
        if transfer is None:
            return None

        reported = transfer.get("percentComplete")
        if isinstance(reported, int | float) and not isinstance(reported, bool):
            return max(0.0, min(100.0, float(reported)))

        transferred = transfer.get("bytesTransferred")
        size = transfer.get("size")
        if (
            isinstance(transferred, int | float)
            and isinstance(size, int | float)
            and not isinstance(transferred, bool)
            and not isinstance(size, bool)
            and size > 0
        ):
            return max(0.0, min(100.0, float(transferred) / float(size) * 100.0))
        return None
