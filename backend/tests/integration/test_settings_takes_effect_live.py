"""End-to-end proof (backend-level): a setting saved through the admin settings API takes
effect on the very next request, with no process restart, no app re-creation, and no cache
clear in between -- the mission's core "no restart needed" requirement.

Drives the real app instance (the same `app_client` fixture every other integration test
uses) through the actual HTTP routes an admin's browser would call: read /api/health (not
configured), save Prowlarr settings via PUT /api/admin/settings/prowlarr, then read
/api/health again in the same test (same running app, same DB engine, no fixture re-entry)
and confirm `configured` has flipped to true. This is the same proof point the Playwright
suite's demo-settings test exercises through the real browser UI; this version isolates the
backend precedence/refresh logic from the web client so a UI bug can never mask a backend
regression or vice versa.
"""

from __future__ import annotations

import re

import pytest

from tests.integration.conftest import make_admin_headers

pytestmark = pytest.mark.asyncio


class TestSettingsTakeEffectWithoutRestart:
    async def test_prowlarr_configured_flips_true_after_ui_save_no_restart(
        self, app_client, monkeypatch
    ):
        monkeypatch.delenv("PROWLARR_URL", raising=False)
        monkeypatch.delenv("PROWLARR_API_KEY", raising=False)
        headers = await make_admin_headers(app_client)

        before = await app_client.get("/api/health")
        assert before.json()["integrations"]["prowlarr"]["configured"] is False

        save_resp = await app_client.put(
            "/api/admin/settings/prowlarr",
            json={
                "prowlarr_url": "http://localhost:19697",
                "prowlarr_api_key": "freshly-saved-key",
            },
            headers=headers,
        )
        assert save_resp.status_code == 200

        after = await app_client.get("/api/health")
        assert after.json()["integrations"]["prowlarr"]["configured"] is True

    async def test_slskd_configured_flips_true_after_ui_save_no_restart(
        self, app_client, monkeypatch
    ):
        monkeypatch.delenv("SLSKD_URL", raising=False)
        monkeypatch.delenv("SLSKD_API_KEY", raising=False)
        headers = await make_admin_headers(app_client)

        before = await app_client.get("/api/health")
        assert before.json()["integrations"]["slskd"]["configured"] is False

        await app_client.put(
            "/api/admin/settings/slskd",
            json={"slskd_url": "http://localhost:15032", "slskd_api_key": "a-key"},
            headers=headers,
        )

        after = await app_client.get("/api/health")
        assert after.json()["integrations"]["slskd"]["configured"] is True

    async def test_health_reachable_reflects_db_saved_prowlarr_credentials(
        self, app_client, monkeypatch, httpx_mock
    ):
        """Not just `configured`: `/api/health`'s live reachability probe must also use the
        DB-saved value, since that is the boolean an operator actually watches to know
        fulfillment will work."""
        monkeypatch.delenv("PROWLARR_URL", raising=False)
        monkeypatch.delenv("PROWLARR_API_KEY", raising=False)
        headers = await make_admin_headers(app_client)

        await app_client.put(
            "/api/admin/settings/prowlarr",
            json={
                "prowlarr_url": "http://localhost:19698",
                "prowlarr_api_key": "a-key",
            },
            headers=headers,
        )

        httpx_mock.add_response(
            url=re.compile(r"http://localhost:19698/api/v1/system/status.*"),
            json={"version": "1.0"},
        )
        resp = await app_client.get("/api/health")
        body = resp.json()
        assert body["integrations"]["prowlarr"]["configured"] is True
        assert body["integrations"]["prowlarr"]["reachable"] is True

    async def test_search_uses_db_saved_prowlarr_settings_without_restart(
        self, app_client, monkeypatch, httpx_mock
    ):
        """The other live consumer of these settings: the search fan-out must also pick up a
        DB-saved Prowlarr connection immediately, not just /api/health's feature-detection.
        """
        monkeypatch.delenv("PROWLARR_URL", raising=False)
        monkeypatch.delenv("PROWLARR_API_KEY", raising=False)
        headers = await make_admin_headers(app_client)

        await app_client.put(
            "/api/admin/settings/prowlarr",
            json={
                "prowlarr_url": "http://localhost:19699",
                "prowlarr_api_key": "a-key",
            },
            headers=headers,
        )

        httpx_mock.add_response(
            url=re.compile(r"https://itunes\.apple\.com/.*"), json={"results": []}
        )
        httpx_mock.add_response(
            url=re.compile(r"https://musicbrainz\.org/.*"), json={"recordings": []}
        )
        httpx_mock.add_response(
            url=re.compile(r"http://localhost:19699/api/v1/search.*"),
            json=[
                {
                    "guid": "release-1",
                    "title": "Some Track",
                    "artist": "Some Artist",
                    "indexer": "TestIndexer",
                }
            ],
        )

        resp = await app_client.get(
            "/api/search", params={"q": "some track"}, headers=headers
        )
        assert resp.status_code == 200
        by_source = {s["source"]: s for s in resp.json()["sources"]}
        assert by_source["prowlarr"]["ok"] is True
        assert len(by_source["prowlarr"]["results"]) == 1
        assert by_source["prowlarr"]["results"][0]["title"] == "Some Track"
