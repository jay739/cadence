"""Integration tests for the admin-only integration settings routes.

Covers: masked GET responses, non-admin (and unauthenticated) rejection, per-integration save
via PUT, and the test-connection action against mocked upstream HTTP responses (never a real
network call, and never persists anything regardless of outcome).
"""

from __future__ import annotations

import re

import pytest

from tests.integration.conftest import make_admin_headers, register_and_login

pytestmark = pytest.mark.asyncio


class TestAuthGuarding:
    async def test_get_settings_requires_auth(self, app_client):
        resp = await app_client.get("/api/admin/settings")
        assert resp.status_code == 401

    async def test_get_settings_rejects_non_admin(self, app_client):
        headers = await register_and_login(
            app_client, "not-admin@example.com", "a-strong-password-1"
        )
        resp = await app_client.get("/api/admin/settings", headers=headers)
        assert resp.status_code == 403

    async def test_update_settings_rejects_non_admin(self, app_client):
        headers = await register_and_login(
            app_client, "not-admin2@example.com", "a-strong-password-1"
        )
        resp = await app_client.put(
            "/api/admin/settings/prowlarr",
            json={"prowlarr_url": "http://prowlarr:9696", "prowlarr_api_key": "key"},
            headers=headers,
        )
        assert resp.status_code == 403

    async def test_test_connection_rejects_non_admin(self, app_client):
        headers = await register_and_login(
            app_client, "not-admin3@example.com", "a-strong-password-1"
        )
        resp = await app_client.post(
            "/api/admin/settings/prowlarr/test",
            json={"prowlarr_url": "http://prowlarr:9696", "prowlarr_api_key": "key"},
            headers=headers,
        )
        assert resp.status_code == 403

    async def test_admin_can_read_settings(self, app_client):
        headers = await make_admin_headers(app_client)
        resp = await app_client.get("/api/admin/settings", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {
            "prowlarr",
            "qbittorrent",
            "slskd",
            "spotify",
            "library",
        }


class TestGetSettingsShape:
    async def test_unconfigured_fields_are_unset(self, app_client):
        headers = await make_admin_headers(app_client)
        resp = await app_client.get("/api/admin/settings", headers=headers)
        body = resp.json()
        assert body["prowlarr"]["prowlarr_url"]["value"] is None
        assert body["prowlarr"]["prowlarr_url"]["source"] == "unset"
        assert body["prowlarr"]["prowlarr_api_key"]["is_secret"] is True
        assert body["prowlarr"]["prowlarr_url"]["is_secret"] is False


class TestUpdateAndMasking:
    async def test_saving_a_secret_then_reading_returns_masked_value_never_plaintext(
        self, app_client
    ):
        headers = await make_admin_headers(app_client)
        put_resp = await app_client.put(
            "/api/admin/settings/prowlarr",
            json={
                "prowlarr_url": "http://localhost:19696",
                "prowlarr_api_key": "super-secret-real-api-key-value",
            },
            headers=headers,
        )
        assert put_resp.status_code == 200
        put_body = put_resp.json()
        assert "super-secret-real-api-key-value" not in put_resp.text
        assert put_body["prowlarr"]["prowlarr_api_key"]["value"] != (
            "super-secret-real-api-key-value"
        )
        assert put_body["prowlarr"]["prowlarr_api_key"]["value"].endswith("alue")
        assert (
            put_body["prowlarr"]["prowlarr_url"]["value"]
            == "http://localhost:19696"
        )

        get_resp = await app_client.get("/api/admin/settings", headers=headers)
        assert "super-secret-real-api-key-value" not in get_resp.text
        get_body = get_resp.json()
        assert get_body["prowlarr"]["prowlarr_api_key"]["source"] == "db"

    async def test_partial_update_leaves_other_fields_unchanged(self, app_client):
        headers = await make_admin_headers(app_client)
        await app_client.put(
            "/api/admin/settings/qbittorrent",
            json={
                "qbittorrent_url": "http://localhost:18080",
                "qbittorrent_username": "admin",
                "qbittorrent_password": "hunter2",
            },
            headers=headers,
        )
        # Second PUT only touches the URL; username/password must survive untouched.
        resp = await app_client.put(
            "/api/admin/settings/qbittorrent",
            json={"qbittorrent_url": "http://localhost:18081"},
            headers=headers,
        )
        body = resp.json()
        assert (
            body["qbittorrent"]["qbittorrent_url"]["value"]
            == "http://localhost:18081"
        )
        assert body["qbittorrent"]["qbittorrent_username"]["value"] == "admin"
        assert body["qbittorrent"]["qbittorrent_password"]["source"] == "db"

    async def test_update_library_settings(self, app_client, monkeypatch):
        # The integration test harness (tests/integration/conftest.py) always sets
        # MUSIC_LIBRARY_PATH so path-safety tests elsewhere have a stable value; since env vars
        # always win per the precedence rule, it must be cleared here to observe the DB value
        # actually winning for this field specifically.
        monkeypatch.delenv("MUSIC_LIBRARY_PATH", raising=False)
        headers = await make_admin_headers(app_client)
        resp = await app_client.put(
            "/api/admin/settings/library",
            json={
                "music_library_path": "/data/library",
                "library_path_pattern": "{artist}/{album}/{title}.{ext}",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["library"]["music_library_path"]["value"] == "/data/library"
        assert (
            body["library"]["library_path_pattern"]["value"]
            == "{artist}/{album}/{title}.{ext}"
        )

    async def test_env_var_takes_precedence_over_saved_db_value_in_the_response(
        self, app_client, monkeypatch
    ):
        headers = await make_admin_headers(app_client)
        await app_client.put(
            "/api/admin/settings/slskd",
            json={"slskd_url": "http://localhost:15030", "slskd_api_key": "db-key"},
            headers=headers,
        )
        monkeypatch.setenv("SLSKD_URL", "http://localhost:15031")
        resp = await app_client.get("/api/admin/settings", headers=headers)
        body = resp.json()
        assert body["slskd"]["slskd_url"]["value"] == "http://localhost:15031"
        assert body["slskd"]["slskd_url"]["source"] == "env"
        monkeypatch.delenv("SLSKD_URL", raising=False)


class TestTestConnection:
    async def test_prowlarr_test_connection_success(self, app_client, httpx_mock):
        headers = await make_admin_headers(app_client)
        httpx_mock.add_response(
            url=re.compile(r"http://localhost:19696/api/v1/system/status.*"),
            json={"version": "1.0"},
        )
        resp = await app_client.post(
            "/api/admin/settings/prowlarr/test",
            json={
                "prowlarr_url": "http://localhost:19696",
                "prowlarr_api_key": "some-key",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True

    async def test_prowlarr_test_connection_reports_auth_failure(
        self, app_client, httpx_mock
    ):
        headers = await make_admin_headers(app_client)
        httpx_mock.add_response(
            url=re.compile(r"http://localhost:19696/api/v1/system/status.*"),
            status_code=401,
        )
        resp = await app_client.post(
            "/api/admin/settings/prowlarr/test",
            json={
                "prowlarr_url": "http://localhost:19696",
                "prowlarr_api_key": "wrong-key",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False

    async def test_test_connection_never_persists_anything(
        self, app_client, httpx_mock
    ):
        headers = await make_admin_headers(app_client)
        httpx_mock.add_response(
            url=re.compile(r"http://localhost:19696/api/v1/system/status.*"),
            json={"version": "1.0"},
        )
        await app_client.post(
            "/api/admin/settings/prowlarr/test",
            json={
                "prowlarr_url": "http://localhost:19696",
                "prowlarr_api_key": "tested-but-not-saved",
            },
            headers=headers,
        )
        get_resp = await app_client.get("/api/admin/settings", headers=headers)
        body = get_resp.json()
        assert body["prowlarr"]["prowlarr_url"]["source"] == "unset"
        assert body["prowlarr"]["prowlarr_url"]["value"] is None

    async def test_test_connection_missing_fields_reports_failure_without_network_call(
        self, app_client
    ):
        headers = await make_admin_headers(app_client)
        resp = await app_client.post(
            "/api/admin/settings/spotify/test",
            json={"spotify_client_id": None, "spotify_client_secret": None},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    async def test_test_connection_unknown_integration_404s(self, app_client):
        headers = await make_admin_headers(app_client)
        resp = await app_client.post(
            "/api/admin/settings/not-a-real-integration/test",
            json={},
            headers=headers,
        )
        assert resp.status_code == 404


class TestValidation:
    async def test_url_too_long_rejected(self, app_client):
        headers = await make_admin_headers(app_client)
        resp = await app_client.put(
            "/api/admin/settings/prowlarr",
            json={"prowlarr_url": "http://" + ("a" * 3000) + ".example"},
            headers=headers,
        )
        assert resp.status_code == 422
