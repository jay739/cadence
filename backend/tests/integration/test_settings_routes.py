"""Integration tests for the admin-only integration settings routes.

Covers: masked GET responses, non-admin (and unauthenticated) rejection, per-integration save
via PUT, and the test-connection action against mocked upstream HTTP responses (never a real
network call, and never persists anything regardless of outcome).
"""

from __future__ import annotations

import re

import pytest

from app.core.config import get_settings
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
            "source_preferences",
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
        assert put_body["prowlarr"]["prowlarr_url"]["value"] == "http://localhost:19696"

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
            body["qbittorrent"]["qbittorrent_url"]["value"] == "http://localhost:18081"
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


class TestSourcePreference:
    """Issue #18: per-integration choice of which source wins, plus the break-glass override."""

    async def test_defaults_to_env_with_no_override_active(self, app_client):
        headers = await make_admin_headers(app_client)
        resp = await app_client.get("/api/admin/settings", headers=headers)
        body = resp.json()
        assert body["source_preferences"]["prowlarr"] == {
            "preference": "env",
            "forced_env": False,
        }

    async def test_setting_database_preference_makes_db_value_win_over_env(
        self, app_client, monkeypatch
    ):
        headers = await make_admin_headers(app_client)
        await app_client.put(
            "/api/admin/settings/slskd",
            json={"slskd_url": "http://localhost:15030", "slskd_api_key": "db-key"},
            headers=headers,
        )
        monkeypatch.setenv("SLSKD_URL", "http://localhost:19999")

        # Before the preference flip: env wins, matching existing default behavior.
        before = await app_client.get("/api/admin/settings", headers=headers)
        assert before.json()["slskd"]["slskd_url"]["value"] == "http://localhost:19999"
        assert before.json()["slskd"]["slskd_url"]["source"] == "env"

        pref_resp = await app_client.put(
            "/api/admin/settings/slskd/source",
            json={"preference": "database"},
            headers=headers,
        )
        assert pref_resp.status_code == 200
        assert pref_resp.json()["source_preferences"]["slskd"] == {
            "preference": "database",
            "forced_env": False,
        }

        after = await app_client.get("/api/admin/settings", headers=headers)
        body = after.json()
        # The env var is still set, but must be ignored now that this integration prefers DB.
        assert body["slskd"]["slskd_url"]["value"] == "http://localhost:15030"
        assert body["slskd"]["slskd_url"]["source"] == "db"
        monkeypatch.delenv("SLSKD_URL", raising=False)

    async def test_break_glass_override_forces_env_regardless_of_stored_preference(
        self, app_client, monkeypatch
    ):
        headers = await make_admin_headers(app_client)
        await app_client.put(
            "/api/admin/settings/prowlarr",
            json={
                "prowlarr_url": "http://localhost:19696",
                "prowlarr_api_key": "db-key",
            },
            headers=headers,
        )
        await app_client.put(
            "/api/admin/settings/prowlarr/source",
            json={"preference": "database"},
            headers=headers,
        )
        monkeypatch.setenv("PROWLARR_URL", "http://localhost:29696")
        monkeypatch.setenv("SETTINGS_FORCE_ENV_PRECEDENCE", "true")
        # Unlike the FIELD_REGISTRY fields (which settings_store reads straight from
        # os.environ on every call, deliberately live-refreshable), settings_force_env_precedence
        # is an ordinary cached Settings field, by design: a break-glass recovery path should
        # not depend on the same live-refresh machinery it might be recovering from. That means
        # it only takes effect on the next Settings() build, exactly like a real restart.
        get_settings.cache_clear()

        resp = await app_client.get("/api/admin/settings", headers=headers)
        body = resp.json()
        # Forced back to env despite the stored "database" preference...
        assert body["prowlarr"]["prowlarr_url"]["value"] == "http://localhost:29696"
        assert body["prowlarr"]["prowlarr_url"]["source"] == "env"
        # ...but the UI must still show "database" as the saved choice, not silently reset it,
        # so turning the override off later takes effect without re-saving anything.
        assert body["source_preferences"]["prowlarr"] == {
            "preference": "database",
            "forced_env": True,
        }
        monkeypatch.delenv("PROWLARR_URL", raising=False)
        monkeypatch.delenv("SETTINGS_FORCE_ENV_PRECEDENCE", raising=False)
        get_settings.cache_clear()

    async def test_unknown_integration_404s(self, app_client):
        headers = await make_admin_headers(app_client)
        resp = await app_client.put(
            "/api/admin/settings/not-a-real-integration/source",
            json={"preference": "database"},
            headers=headers,
        )
        assert resp.status_code == 404

    async def test_invalid_preference_value_rejected(self, app_client):
        headers = await make_admin_headers(app_client)
        resp = await app_client.put(
            "/api/admin/settings/prowlarr/source",
            json={"preference": "somewhere-else"},
            headers=headers,
        )
        assert resp.status_code == 422

    async def test_non_admin_cannot_set_source_preference(self, app_client):
        headers = await register_and_login(
            app_client, "not-admin-source@example.com", "a-strong-password-1"
        )
        resp = await app_client.put(
            "/api/admin/settings/prowlarr/source",
            json={"preference": "database"},
            headers=headers,
        )
        assert resp.status_code == 403


class TestExplicitClear:
    """Issue #18's other fix: a field explicitly sent as null must actually clear it, distinct
    from a field the client simply didn't include in the request body."""

    async def test_field_sent_as_null_is_cleared(self, app_client):
        headers = await make_admin_headers(app_client)
        await app_client.put(
            "/api/admin/settings/prowlarr",
            json={
                "prowlarr_url": "http://localhost:19696",
                "prowlarr_api_key": "a-key",
            },
            headers=headers,
        )
        resp = await app_client.put(
            "/api/admin/settings/prowlarr",
            json={"prowlarr_url": None},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["prowlarr"]["prowlarr_url"]["value"] is None
        assert body["prowlarr"]["prowlarr_url"]["source"] == "unset"
        # The field that was simply omitted (not sent as null) must be untouched.
        assert body["prowlarr"]["prowlarr_api_key"]["source"] == "db"

    async def test_field_omitted_entirely_is_left_untouched(self, app_client):
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
        # An empty body: every field omitted, nothing should change (this is also the existing
        # regression the old `if value is None: continue` behavior relied on, now driven by
        # model_fields_set instead).
        resp = await app_client.put(
            "/api/admin/settings/qbittorrent", json={}, headers=headers
        )
        body = resp.json()
        assert (
            body["qbittorrent"]["qbittorrent_url"]["value"] == "http://localhost:18080"
        )
        assert body["qbittorrent"]["qbittorrent_username"]["value"] == "admin"
        assert body["qbittorrent"]["qbittorrent_password"]["source"] == "db"


class TestValidation:
    async def test_url_too_long_rejected(self, app_client):
        headers = await make_admin_headers(app_client)
        resp = await app_client.put(
            "/api/admin/settings/prowlarr",
            json={"prowlarr_url": "http://" + ("a" * 3000) + ".example"},
            headers=headers,
        )
        assert resp.status_code == 422
