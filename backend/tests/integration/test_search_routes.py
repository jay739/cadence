from __future__ import annotations

import pytest

from tests.integration.conftest import register_and_login

pytestmark = pytest.mark.asyncio


class TestSearchAuth:
    async def test_search_requires_authentication(self, app_client):
        resp = await app_client.get("/api/search", params={"q": "test"})
        assert resp.status_code == 401


class TestSearchDemoMode:
    async def test_search_returns_all_four_sources_in_demo_mode(self, demo_app_client):
        headers = await register_and_login(
            demo_app_client, "searchtest@example.com", "a-strong-password-1"
        )
        resp = await demo_app_client.get(
            "/api/search", params={"q": "Tum Hi Ho"}, headers=headers
        )
        assert resp.status_code == 200
        body = resp.json()
        source_names = {s["source"] for s in body["sources"]}
        assert source_names == {"apple", "musicbrainz", "spotify", "prowlarr"}
        # Demo mode must be usable with zero external configuration: every source ok.
        assert all(s["ok"] for s in body["sources"])

    async def test_search_validates_query_length(self, demo_app_client):
        headers = await register_and_login(
            demo_app_client, "lentest@example.com", "a-strong-password-1"
        )
        resp = await demo_app_client.get(
            "/api/search", params={"q": ""}, headers=headers
        )
        assert resp.status_code == 422

    async def test_search_rejects_overlong_query(self, demo_app_client):
        headers = await register_and_login(
            demo_app_client, "overlong@example.com", "a-strong-password-1"
        )
        resp = await demo_app_client.get(
            "/api/search", params={"q": "x" * 400}, headers=headers
        )
        assert resp.status_code == 422

    async def test_search_limit_is_bounded(self, demo_app_client):
        headers = await register_and_login(
            demo_app_client, "limittest@example.com", "a-strong-password-1"
        )
        resp = await demo_app_client.get(
            "/api/search", params={"q": "test", "limit": 999}, headers=headers
        )
        assert resp.status_code == 422


class TestSearchRateLimit:
    async def test_search_rate_limited_after_threshold(self, demo_app_client):
        headers = await register_and_login(
            demo_app_client, "ratelimit@example.com", "a-strong-password-1"
        )
        statuses = []
        for _ in range(35):
            resp = await demo_app_client.get(
                "/api/search", params={"q": "test"}, headers=headers
            )
            statuses.append(resp.status_code)
        assert 429 in statuses
