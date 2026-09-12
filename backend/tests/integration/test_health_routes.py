from __future__ import annotations

import pytest

from tests.integration.conftest import make_admin_headers, register_and_login

pytestmark = pytest.mark.asyncio


class TestHealth:
    async def test_health_is_public(self, app_client):
        resp = await app_client.get("/api/health")
        assert resp.status_code == 200

    async def test_health_reports_version_and_mode(self, app_client):
        resp = await app_client.get("/api/health")
        body = resp.json()
        assert body["version"]
        assert body["mode"] == "live"
        assert body["database_ok"] is True

    async def test_health_demo_mode_reported(self, demo_app_client):
        resp = await demo_app_client.get("/api/health")
        assert resp.json()["mode"] == "demo"

    async def test_health_reports_all_integrations_unconfigured_by_default(
        self, app_client
    ):
        headers = await make_admin_headers(app_client)
        resp = await app_client.get("/api/health/integrations", headers=headers)
        integrations = resp.json()["integrations"]
        assert set(integrations.keys()) == {
            "prowlarr",
            "qbittorrent",
            "slskd",
            "spotify",
            "navidrome",
            "ytdlp",
        }
        for status in integrations.values():
            assert status["configured"] is False

    async def test_ytdlp_configured_when_url_is_set(self, app_client, monkeypatch):
        monkeypatch.setenv("YTDLP_SHIM_URL", "http://ytdlp-shim.internal:8090")
        from app.core.config import get_settings

        get_settings.cache_clear()
        headers = await make_admin_headers(app_client)
        resp = await app_client.get("/api/health/integrations", headers=headers)
        assert resp.json()["integrations"]["ytdlp"]["configured"] is True

    async def test_ytdlp_never_leaks_its_url(self, app_client, monkeypatch):
        monkeypatch.setenv("YTDLP_SHIM_URL", "http://internal-ytdlp-shim.example:8090")
        from app.core.config import get_settings

        get_settings.cache_clear()
        headers = await make_admin_headers(app_client)
        resp = await app_client.get("/api/health/integrations", headers=headers)
        assert "internal-ytdlp-shim" not in resp.text

    async def test_navidrome_login_availability_is_public(self, app_client, monkeypatch):
        # The login page reads this pre-auth to decide whether to offer a Navidrome sign-in
        # tab at all, so unlike the rest of the integration detail it has to stay readable
        # with no credentials, and flip true purely from the URL being set.
        monkeypatch.setenv("NAVIDROME_URL", "http://navidrome.internal:4533")
        from app.core.config import get_settings

        get_settings.cache_clear()
        resp = await app_client.get("/api/health")
        assert resp.json()["navidrome_login_available"] is True

    async def test_navidrome_never_leaks_its_url(self, app_client, monkeypatch):
        monkeypatch.setenv("NAVIDROME_URL", "http://internal-navidrome.example:4533")
        from app.core.config import get_settings

        get_settings.cache_clear()
        headers = await make_admin_headers(app_client)
        for path in ("/api/health", "/api/health/integrations"):
            resp = await app_client.get(path, headers=headers)
            assert "internal-navidrome" not in resp.text

    async def test_health_never_leaks_secrets_or_urls(self, app_client, monkeypatch):
        monkeypatch.setenv("PROWLARR_URL", "http://internal-prowlarr.example:9696")
        monkeypatch.setenv("PROWLARR_API_KEY", "super-secret-key-value")
        from app.core.config import get_settings

        get_settings.cache_clear()
        headers = await make_admin_headers(app_client)
        for path in ("/api/health", "/api/health/integrations"):
            resp = await app_client.get(path, headers=headers)
            assert "super-secret-key-value" not in resp.text
            assert "internal-prowlarr.example" not in resp.text
        get_settings.cache_clear()


class TestSecurityHeaders:
    async def test_csp_header_present(self, app_client):
        resp = await app_client.get("/api/health")
        assert "Content-Security-Policy" in resp.headers
        assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]

    async def test_x_content_type_options_present(self, app_client):
        resp = await app_client.get("/api/health")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    async def test_referrer_policy_present(self, app_client):
        resp = await app_client.get("/api/health")
        assert resp.headers["Referrer-Policy"] == "no-referrer"

    async def test_frame_options_deny(self, app_client):
        resp = await app_client.get("/api/health")
        assert resp.headers["X-Frame-Options"] == "DENY"


class TestCacheControlHeaderWiredEndToEnd:
    async def test_api_response_carries_no_store(self, app_client):
        resp = await app_client.get("/api/health")
        assert resp.headers["cache-control"] == "no-store"


class TestIntegrationDetailIsAdminOnly:
    """Server-level detail must not be readable by a general user, and especially not
    anonymously: /api/health is reachable by anyone who can reach the port."""

    async def test_public_health_exposes_no_integration_detail(self, app_client):
        resp = await app_client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "integrations" not in body
        assert set(body.keys()) == {
            "version",
            "mode",
            "database_ok",
            "navidrome_login_available",
            "spotify_search_available",
        }
        # Not even the names of the operator's stack.
        lowered = resp.text.lower()
        for leaked in ("prowlarr", "qbittorrent", "slskd", "ytdlp", "reachable"):
            assert leaked not in lowered

    async def test_integration_detail_requires_auth(self, app_client):
        resp = await app_client.get("/api/health/integrations")
        assert resp.status_code == 401

    async def test_integration_detail_rejects_a_general_user(self, app_client):
        headers = await register_and_login(
            app_client, "plain-user-health@example.com", "a-strong-password-1"
        )
        resp = await app_client.get("/api/health/integrations", headers=headers)
        assert resp.status_code == 403

    async def test_admin_still_gets_the_full_picture(self, app_client):
        headers = await make_admin_headers(app_client)
        resp = await app_client.get("/api/health/integrations", headers=headers)
        assert resp.status_code == 200
        assert set(resp.json()["integrations"].keys()) == {
            "prowlarr",
            "qbittorrent",
            "slskd",
            "spotify",
            "navidrome",
            "ytdlp",
        }
