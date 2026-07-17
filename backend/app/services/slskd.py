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

    async def is_download_complete(self, username: str, filename: str) -> bool:
        if not self.configured:
            return False
        resp = await self._http.get(
            f"{self._base_url}/api/v0/transfers/downloads/{username}",
            policy=POLICY_ALLOW_PRIVATE,
            headers=self._headers(),
        )
        if resp.status_code != 200:
            return False
        data = resp.json()
        for directory in data.get("directories", []):
            for f in directory.get("files", []):
                if f.get("filename") == filename:
                    return f.get("state", "").lower().endswith("completed")
        return False
