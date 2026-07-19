"""Integration tests for the opt-in per-user Spotify personalization routes.

Exercises the real app through its HTTP routes (per this repo's existing pattern), with Spotify
itself mocked at the HTTP layer via pytest-httpx. Covers: unauthenticated rejection, the
not-configured guard, the full connect -> callback -> status round trip, disconnect, the
CSRF-style state validation, and the saved-tracks/playlists quick-pick routes.
"""

from __future__ import annotations

import re

import pytest

from app.core.config import get_settings
from app.core.security import create_oauth_state_token
from app.services import spotify_oauth
from tests.integration.conftest import register_and_login

pytestmark = pytest.mark.asyncio


async def _configure_spotify(app_client, headers) -> None:
    resp = await app_client.put(
        "/api/admin/settings/spotify",
        json={
            "spotify_client_id": "test-client-id",
            "spotify_client_secret": "test-secret",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


class TestStatusRoute:
    async def test_requires_auth(self, app_client):
        resp = await app_client.get("/api/spotify/status")
        assert resp.status_code == 401

    async def test_not_connected_by_default(self, app_client):
        headers = await register_and_login(
            app_client, "listener@example.com", "a-strong-pw-1"
        )
        resp = await app_client.get("/api/spotify/status", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"connected": False, "display_name": None}


class TestConnectRoute:
    async def test_requires_auth(self, app_client):
        resp = await app_client.get("/api/spotify/connect", follow_redirects=False)
        assert resp.status_code == 401

    async def test_400_when_spotify_not_configured(self, app_client):
        headers = await register_and_login(
            app_client, "listener@example.com", "a-strong-pw-1"
        )
        resp = await app_client.get(
            "/api/spotify/connect", headers=headers, follow_redirects=False
        )
        assert resp.status_code == 400

    async def test_redirects_to_spotify_authorize_when_configured(
        self, app_client, monkeypatch
    ):
        monkeypatch.setenv("APP_BASE_URL", "https://cadence.example.internal")
        get_settings.cache_clear()

        from tests.integration.conftest import make_admin_headers

        admin_headers = await make_admin_headers(app_client)
        await _configure_spotify(app_client, admin_headers)

        headers = await register_and_login(
            app_client, "listener2@example.com", "a-strong-pw-1"
        )
        resp = await app_client.get(
            "/api/spotify/connect", headers=headers, follow_redirects=False
        )
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert location.startswith(spotify_oauth.SPOTIFY_AUTHORIZE_URL)
        assert "client_id=test-client-id" in location
        assert "state=" in location
        get_settings.cache_clear()


class TestCallbackRoute:
    async def test_missing_code_redirects_with_error(self, app_client):
        resp = await app_client.get(
            "/api/spotify/callback",
            params={"error": "access_denied"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "spotify_connect=error" in resp.headers["location"]

    async def test_invalid_state_redirects_with_error(self, app_client):
        resp = await app_client.get(
            "/api/spotify/callback",
            params={"code": "some-code", "state": "not-a-valid-token"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "spotify_connect=error" in resp.headers["location"]

    async def test_full_round_trip_connects_and_status_reflects_it(
        self, app_client, monkeypatch, httpx_mock
    ):
        monkeypatch.setenv("APP_BASE_URL", "https://cadence.example.internal")
        get_settings.cache_clear()

        from tests.integration.conftest import make_admin_headers

        admin_headers = await make_admin_headers(app_client)
        await _configure_spotify(app_client, admin_headers)

        headers = await register_and_login(
            app_client, "listener3@example.com", "a-strong-pw-1"
        )
        me_resp = await app_client.get("/api/auth/me", headers=headers)
        user_id = me_resp.json()["id"]

        settings = get_settings()
        state = create_oauth_state_token(
            user_id=user_id, secret=settings.resolved_session_secret()
        )

        httpx_mock.add_response(
            url=spotify_oauth.SPOTIFY_TOKEN_URL,
            method="POST",
            json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600},
        )

        callback_resp = await app_client.get(
            "/api/spotify/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )
        assert callback_resp.status_code == 302
        assert "spotify_connect=success" in callback_resp.headers["location"]

        httpx_mock.add_response(
            url=spotify_oauth.SPOTIFY_ME_URL,
            json={"display_name": "Listener Three"},
        )
        status_resp = await app_client.get("/api/spotify/status", headers=headers)
        assert status_resp.status_code == 200
        body = status_resp.json()
        assert body["connected"] is True
        assert body["display_name"] == "Listener Three"
        get_settings.cache_clear()

    async def test_state_bound_to_a_different_user_is_rejected_by_lookup(
        self, app_client, monkeypatch
    ):
        """A state token is only valid if its user_id resolves to a real user; a token for a
        made-up user id must not silently attach tokens to whichever account happens to be
        making the callback request (there is none here, since /callback has no session).
        """
        monkeypatch.setenv("APP_BASE_URL", "https://cadence.example.internal")
        get_settings.cache_clear()
        settings = get_settings()
        bogus_state = create_oauth_state_token(
            user_id="00000000-0000-0000-0000-000000000000",
            secret=settings.resolved_session_secret(),
        )
        resp = await app_client.get(
            "/api/spotify/callback",
            params={"code": "auth-code", "state": bogus_state},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "spotify_connect=error" in resp.headers["location"]
        get_settings.cache_clear()


class TestDisconnectRoute:
    async def test_requires_auth(self, app_client):
        resp = await app_client.post("/api/spotify/disconnect")
        assert resp.status_code == 401

    async def test_disconnect_clears_connection(
        self, app_client, monkeypatch, httpx_mock
    ):
        monkeypatch.setenv("APP_BASE_URL", "https://cadence.example.internal")
        get_settings.cache_clear()

        from tests.integration.conftest import make_admin_headers

        admin_headers = await make_admin_headers(app_client)
        await _configure_spotify(app_client, admin_headers)

        headers = await register_and_login(
            app_client, "listener4@example.com", "a-strong-pw-1"
        )
        me_resp = await app_client.get("/api/auth/me", headers=headers)
        user_id = me_resp.json()["id"]
        settings = get_settings()
        state = create_oauth_state_token(
            user_id=user_id, secret=settings.resolved_session_secret()
        )
        httpx_mock.add_response(
            url=spotify_oauth.SPOTIFY_TOKEN_URL,
            method="POST",
            json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600},
        )
        await app_client.get(
            "/api/spotify/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )

        disconnect_resp = await app_client.post(
            "/api/spotify/disconnect", headers=headers
        )
        assert disconnect_resp.status_code == 204

        status_resp = await app_client.get("/api/spotify/status", headers=headers)
        assert status_resp.json() == {"connected": False, "display_name": None}
        get_settings.cache_clear()


class TestLibraryRoutes:
    async def test_saved_tracks_400s_when_spotify_not_configured(self, app_client):
        headers = await register_and_login(
            app_client, "listener5a@example.com", "a-strong-pw-1"
        )
        resp = await app_client.get(
            "/api/spotify/library/saved-tracks", headers=headers
        )
        assert resp.status_code == 400

    async def test_saved_tracks_404s_when_configured_but_not_connected(
        self, app_client
    ):
        from tests.integration.conftest import make_admin_headers

        admin_headers = await make_admin_headers(app_client)
        await _configure_spotify(app_client, admin_headers)

        headers = await register_and_login(
            app_client, "listener5b@example.com", "a-strong-pw-1"
        )
        resp = await app_client.get(
            "/api/spotify/library/saved-tracks", headers=headers
        )
        assert resp.status_code == 404

    async def test_playlists_requires_auth(self, app_client):
        resp = await app_client.get("/api/spotify/library/playlists")
        assert resp.status_code == 401

    async def _connect(self, app_client, monkeypatch, httpx_mock, email: str) -> dict:
        monkeypatch.setenv("APP_BASE_URL", "https://cadence.example.internal")
        get_settings.cache_clear()

        from tests.integration.conftest import make_admin_headers

        admin_headers = await make_admin_headers(app_client)
        await _configure_spotify(app_client, admin_headers)

        headers = await register_and_login(app_client, email, "a-strong-pw-1")
        me_resp = await app_client.get("/api/auth/me", headers=headers)
        user_id = me_resp.json()["id"]
        settings = get_settings()
        state = create_oauth_state_token(
            user_id=user_id, secret=settings.resolved_session_secret()
        )
        httpx_mock.add_response(
            url=spotify_oauth.SPOTIFY_TOKEN_URL,
            method="POST",
            json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600},
        )
        await app_client.get(
            "/api/spotify/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )
        return headers

    async def test_saved_tracks_returns_normalized_results_once_connected(
        self, app_client, monkeypatch, httpx_mock
    ):
        headers = await self._connect(
            app_client, monkeypatch, httpx_mock, "listener6@example.com"
        )
        httpx_mock.add_response(
            url=re.compile(re.escape(spotify_oauth.SPOTIFY_SAVED_TRACKS_URL) + r".*"),
            json={
                "items": [
                    {
                        "track": {
                            "id": "trk1",
                            "name": "Some Song",
                            "artists": [{"name": "Some Artist"}],
                            "album": {"name": "Some Album", "images": []},
                            "duration_ms": 180_000,
                        }
                    }
                ]
            },
        )
        resp = await app_client.get(
            "/api/spotify/library/saved-tracks", headers=headers
        )
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 1
        assert results[0]["source"] == "spotify"
        assert results[0]["source_id"] == "trk1"
        get_settings.cache_clear()

    async def test_playlist_tracks_rejects_malformed_playlist_id(self, app_client):
        headers = await register_and_login(
            app_client, "listener7@example.com", "a-strong-pw-1"
        )
        resp = await app_client.get(
            "/api/spotify/library/playlists/not valid!/tracks", headers=headers
        )
        assert resp.status_code == 422

    async def test_playlists_returns_normalized_results_once_connected(
        self, app_client, monkeypatch, httpx_mock
    ):
        headers = await self._connect(
            app_client, monkeypatch, httpx_mock, "listener8@example.com"
        )
        httpx_mock.add_response(
            url=re.compile(re.escape(spotify_oauth.SPOTIFY_PLAYLISTS_URL) + r".*"),
            json={"items": [{"id": "pl1", "name": "Roadtrip", "tracks": {"total": 5}}]},
        )
        resp = await app_client.get("/api/spotify/library/playlists", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["playlists"] == [
            {"id": "pl1", "name": "Roadtrip", "track_count": 5}
        ]
        get_settings.cache_clear()

    async def test_playlist_tracks_returns_normalized_results_once_connected(
        self, app_client, monkeypatch, httpx_mock
    ):
        headers = await self._connect(
            app_client, monkeypatch, httpx_mock, "listener9@example.com"
        )
        httpx_mock.add_response(
            url=re.compile(r"https://api\.spotify\.com/v1/playlists/pl1/tracks.*"),
            json={
                "items": [
                    {
                        "track": {
                            "id": "trk9",
                            "name": "Playlist Song",
                            "artists": [{"name": "Someone"}],
                            "album": {"name": "An Album", "images": []},
                            "duration_ms": 200_000,
                        }
                    }
                ]
            },
        )
        resp = await app_client.get(
            "/api/spotify/library/playlists/pl1/tracks", headers=headers
        )
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 1
        assert results[0]["source_id"] == "trk9"
        get_settings.cache_clear()


class TestStatusEdgeCases:
    async def test_status_connected_true_when_configured_but_refresh_fails(
        self, app_client, monkeypatch, httpx_mock
    ):
        """A user who connected while Spotify was configured, whose refresh token has since
        gone bad (revoked on Spotify's side), still shows as connected=True (the app-side
        connection record exists) rather than silently reporting disconnected.
        """
        monkeypatch.setenv("APP_BASE_URL", "https://cadence.example.internal")
        get_settings.cache_clear()

        from tests.integration.conftest import make_admin_headers

        admin_headers = await make_admin_headers(app_client)
        await _configure_spotify(app_client, admin_headers)

        headers = await register_and_login(
            app_client, "listener10@example.com", "a-strong-pw-1"
        )
        me_resp = await app_client.get("/api/auth/me", headers=headers)
        user_id = me_resp.json()["id"]
        settings = get_settings()
        state = create_oauth_state_token(
            user_id=user_id, secret=settings.resolved_session_secret()
        )
        httpx_mock.add_response(
            url=spotify_oauth.SPOTIFY_TOKEN_URL,
            method="POST",
            json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": -10},
        )
        await app_client.get(
            "/api/spotify/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )

        # Token is already expired (expires_in=-10 above), forcing the status check down the
        # refresh path, which now fails (Spotify has revoked the refresh token on its side).
        httpx_mock.add_response(
            url=spotify_oauth.SPOTIFY_TOKEN_URL, method="POST", status_code=400
        )
        status_resp = await app_client.get("/api/spotify/status", headers=headers)
        assert status_resp.status_code == 200
        assert status_resp.json() == {"connected": True, "display_name": None}
        get_settings.cache_clear()

    async def test_status_connected_true_when_spotify_never_configured(
        self, app_client
    ):
        """A user row can carry Spotify tokens (e.g. restored from a backup taken while
        Spotify was configured) even in a deployment where Spotify has never been configured
        via the admin API in this database. Status must still say connected=True from the
        user-side record alone, without attempting any network call or requiring the
        integration to be configured.
        """
        from cryptography.fernet import Fernet
        from sqlalchemy import select

        from app.core.config import get_settings
        from app.db.base import get_sessionmaker
        from app.models.user import User
        from app.services.spotify_oauth import store_tokens

        headers = await register_and_login(
            app_client, "listener11@example.com", "a-strong-pw-1"
        )
        settings = get_settings()
        key = settings.resolved_settings_encryption_key()
        assert isinstance(key, bytes) and Fernet(
            key
        )  # sanity: a real usable Fernet key

        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            result = await session.execute(
                select(User).where(User.email == "listener11@example.com")
            )
            user = result.scalar_one()
            store_tokens(
                user,
                {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600},
                key=key,
            )
            await session.commit()

        status_resp = await app_client.get("/api/spotify/status", headers=headers)
        assert status_resp.status_code == 200
        assert status_resp.json() == {"connected": True, "display_name": None}


class TestCallbackFailureModes:
    async def test_callback_token_exchange_failure_redirects_with_error(
        self, app_client, monkeypatch, httpx_mock
    ):
        monkeypatch.setenv("APP_BASE_URL", "https://cadence.example.internal")
        get_settings.cache_clear()

        from tests.integration.conftest import make_admin_headers

        admin_headers = await make_admin_headers(app_client)
        await _configure_spotify(app_client, admin_headers)

        headers = await register_and_login(
            app_client, "listener12@example.com", "a-strong-pw-1"
        )
        me_resp = await app_client.get("/api/auth/me", headers=headers)
        user_id = me_resp.json()["id"]
        settings = get_settings()
        state = create_oauth_state_token(
            user_id=user_id, secret=settings.resolved_session_secret()
        )
        httpx_mock.add_response(
            url=spotify_oauth.SPOTIFY_TOKEN_URL, method="POST", status_code=400
        )
        resp = await app_client.get(
            "/api/spotify/callback",
            params={"code": "bad-code", "state": state},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "spotify_connect=error" in resp.headers["location"]
        get_settings.cache_clear()

    async def test_callback_400s_path_when_spotify_unconfigured_between_connect_and_callback(
        self, app_client, monkeypatch
    ):
        """State was signed while Spotify was configured, but by the time the callback lands
        the operator has cleared the settings; the callback must fail closed, not proceed with
        an empty client id/secret.
        """
        monkeypatch.setenv("APP_BASE_URL", "https://cadence.example.internal")
        get_settings.cache_clear()
        settings = get_settings()

        headers = await register_and_login(
            app_client, "listener13@example.com", "a-strong-pw-1"
        )
        me_resp = await app_client.get("/api/auth/me", headers=headers)
        user_id = me_resp.json()["id"]
        state = create_oauth_state_token(
            user_id=user_id, secret=settings.resolved_session_secret()
        )

        resp = await app_client.get(
            "/api/spotify/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "spotify_connect=error" in resp.headers["location"]
        get_settings.cache_clear()


class TestRateLimiting:
    async def test_status_route_is_rate_limited_after_threshold(self, app_client):
        headers = await register_and_login(
            app_client, "listener14@example.com", "a-strong-pw-1"
        )
        statuses = []
        for _ in range(35):
            resp = await app_client.get("/api/spotify/status", headers=headers)
            statuses.append(resp.status_code)
        assert 429 in statuses

    async def test_disconnect_route_is_rate_limited_after_threshold(self, app_client):
        headers = await register_and_login(
            app_client, "listener15@example.com", "a-strong-pw-1"
        )
        statuses = []
        for _ in range(15):
            resp = await app_client.post("/api/spotify/disconnect", headers=headers)
            statuses.append(resp.status_code)
        assert 429 in statuses
