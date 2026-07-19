"""Unit tests for the per-user Spotify personalization OAuth service.

All Spotify HTTP calls are mocked (pytest-httpx). Covers: authorize URL construction, code
exchange, refresh (including Spotify's "no new refresh_token in the response" behavior),
encrypted token storage/retrieval, saved-tracks/playlist normalization, and the not-connected
and refresh-failure paths.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

from app.core.crypto import decrypt_value
from app.core.safe_http import SafeHttpClient
from app.models.user import User, UserRole
from app.services import spotify_oauth

pytestmark = pytest.mark.asyncio


@pytest.fixture
def fernet_key() -> bytes:
    return Fernet.generate_key()


def _make_user(**overrides) -> User:
    defaults = {
        "email": "listener@example.internal",
        "password_hash": "x",
        "role": UserRole.USER,
    }
    defaults.update(overrides)
    return User(**defaults)


class TestAuthorizeUrl:
    def test_build_redirect_uri_requires_app_base_url(self):
        from app.core.config import Settings

        settings = Settings(APP_BASE_URL=None)
        with pytest.raises(spotify_oauth.SpotifyOAuthNotConfiguredError):
            spotify_oauth.build_redirect_uri(settings)

    def test_build_redirect_uri_strips_trailing_slash(self):
        from app.core.config import Settings

        settings = Settings(APP_BASE_URL="https://cadence.example.internal/")
        assert (
            spotify_oauth.build_redirect_uri(settings)
            == "https://cadence.example.internal/api/spotify/callback"
        )

    def test_build_authorize_url_contains_required_params(self):
        url = spotify_oauth.build_authorize_url(
            client_id="abc123",
            redirect_uri="https://cadence.example.internal/api/spotify/callback",
            state="signed-state-token",
        )
        assert url.startswith(spotify_oauth.SPOTIFY_AUTHORIZE_URL)
        assert "client_id=abc123" in url
        assert "state=signed-state-token" in url
        assert "response_type=code" in url
        # Read-only scopes only: nothing here can grant write access to the user's account.
        assert "user-library-read" in url
        assert "playlist-modify" not in url


class TestTokenExchangeAndRefresh:
    async def test_exchange_code_for_tokens_success(self, httpx_mock):
        httpx_mock.add_response(
            url=spotify_oauth.SPOTIFY_TOKEN_URL,
            method="POST",
            json={
                "access_token": "at-1",
                "refresh_token": "rt-1",
                "expires_in": 3600,
            },
        )
        payload = await spotify_oauth.exchange_code_for_tokens(
            code="auth-code",
            redirect_uri="https://cadence.example.internal/api/spotify/callback",
            client_id="cid",
            client_secret="csecret",
            http=SafeHttpClient(),
        )
        assert payload["access_token"] == "at-1"
        assert payload["refresh_token"] == "rt-1"

    async def test_exchange_code_for_tokens_failure_raises(self, httpx_mock):
        httpx_mock.add_response(
            url=spotify_oauth.SPOTIFY_TOKEN_URL, method="POST", status_code=400
        )
        with pytest.raises(spotify_oauth.SpotifyOAuthError):
            await spotify_oauth.exchange_code_for_tokens(
                code="bad-code",
                redirect_uri="https://cadence.example.internal/api/spotify/callback",
                client_id="cid",
                client_secret="csecret",
                http=SafeHttpClient(),
            )

    async def test_refresh_access_token_success(self, httpx_mock):
        httpx_mock.add_response(
            url=spotify_oauth.SPOTIFY_TOKEN_URL,
            method="POST",
            json={"access_token": "at-2", "expires_in": 3600},
        )
        payload = await spotify_oauth.refresh_access_token(
            refresh_token="rt-1",
            client_id="cid",
            client_secret="csecret",
            http=SafeHttpClient(),
        )
        assert payload["access_token"] == "at-2"
        assert "refresh_token" not in payload


class TestStoreTokens:
    def test_store_tokens_encrypts_and_sets_expiry(self, fernet_key):
        user = _make_user()
        spotify_oauth.store_tokens(
            user,
            {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600},
            key=fernet_key,
        )
        assert user.spotify_access_token != "at-1"
        assert user.spotify_refresh_token != "rt-1"
        assert decrypt_value(user.spotify_access_token, key=fernet_key) == "at-1"
        assert decrypt_value(user.spotify_refresh_token, key=fernet_key) == "rt-1"
        assert user.spotify_token_expires_at > datetime.now(UTC)

    def test_store_tokens_keeps_prior_refresh_token_when_absent(self, fernet_key):
        user = _make_user()
        spotify_oauth.store_tokens(
            user,
            {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600},
            key=fernet_key,
        )
        original_refresh = user.spotify_refresh_token

        # Spotify's refresh grant frequently omits refresh_token in the response (the original
        # stays valid); overwriting it with None here would strand the user's connection.
        spotify_oauth.store_tokens(
            user, {"access_token": "at-2", "expires_in": 3600}, key=fernet_key
        )
        assert user.spotify_refresh_token == original_refresh
        assert decrypt_value(user.spotify_access_token, key=fernet_key) == "at-2"


class TestConnectionState:
    def test_is_connected_false_for_fresh_user(self):
        assert spotify_oauth.is_connected(_make_user()) is False

    def test_is_connected_true_after_store_tokens(self, fernet_key):
        user = _make_user()
        spotify_oauth.store_tokens(
            user,
            {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600},
            key=fernet_key,
        )
        assert spotify_oauth.is_connected(user) is True

    def test_disconnect_clears_all_fields(self, fernet_key):
        user = _make_user()
        spotify_oauth.store_tokens(
            user,
            {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600},
            key=fernet_key,
        )
        spotify_oauth.disconnect(user)
        assert user.spotify_access_token is None
        assert user.spotify_refresh_token is None
        assert user.spotify_token_expires_at is None
        assert spotify_oauth.is_connected(user) is False


class TestGetValidAccessToken:
    async def test_returns_none_when_never_connected(self, fernet_key):
        user = _make_user()
        token = await spotify_oauth.get_valid_access_token(
            user,
            client_id="cid",
            client_secret="csecret",
            key=fernet_key,
            http=SafeHttpClient(),
            session=None,  # not reached: no refresh happens for a never-connected user
        )
        assert token is None

    async def test_reuses_still_valid_access_token_without_network_call(
        self, fernet_key, httpx_mock
    ):
        user = _make_user()
        spotify_oauth.store_tokens(
            user,
            {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600},
            key=fernet_key,
        )
        # No httpx_mock response registered: a network call here would fail the test outright,
        # proving the still-valid branch never reaches out to Spotify.
        token = await spotify_oauth.get_valid_access_token(
            user,
            client_id="cid",
            client_secret="csecret",
            key=fernet_key,
            http=SafeHttpClient(),
            session=None,
        )
        assert token == "at-1"

    async def test_refreshes_expired_token_and_commits(self, fernet_key, httpx_mock):
        user = _make_user()
        spotify_oauth.store_tokens(
            user,
            {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600},
            key=fernet_key,
        )
        # Force expiry into the past so the refresh branch is taken.
        user.spotify_token_expires_at = datetime.now(UTC) - timedelta(seconds=1)

        httpx_mock.add_response(
            url=spotify_oauth.SPOTIFY_TOKEN_URL,
            method="POST",
            json={"access_token": "at-2", "expires_in": 3600},
        )

        class _FakeSession:
            committed = False

            async def commit(self) -> None:
                self.committed = True

        fake_session = _FakeSession()
        token = await spotify_oauth.get_valid_access_token(
            user,
            client_id="cid",
            client_secret="csecret",
            key=fernet_key,
            http=SafeHttpClient(),
            session=fake_session,
        )
        assert token == "at-2"
        assert fake_session.committed is True
        assert decrypt_value(user.spotify_access_token, key=fernet_key) == "at-2"
        # The original refresh token must survive since Spotify's refresh response omitted one.
        assert decrypt_value(user.spotify_refresh_token, key=fernet_key) == "rt-1"


class TestFetchDisplayName:
    async def test_success(self, httpx_mock):
        httpx_mock.add_response(
            url=spotify_oauth.SPOTIFY_ME_URL,
            json={"display_name": "Test Listener", "id": "user123"},
        )
        name = await spotify_oauth.fetch_display_name("at-1", http=SafeHttpClient())
        assert name == "Test Listener"

    async def test_falls_back_to_id_when_display_name_missing(self, httpx_mock):
        httpx_mock.add_response(
            url=spotify_oauth.SPOTIFY_ME_URL, json={"id": "user123"}
        )
        name = await spotify_oauth.fetch_display_name("at-1", http=SafeHttpClient())
        assert name == "user123"

    async def test_returns_none_on_failure(self, httpx_mock):
        httpx_mock.add_response(url=spotify_oauth.SPOTIFY_ME_URL, status_code=401)
        name = await spotify_oauth.fetch_display_name("at-1", http=SafeHttpClient())
        assert name is None


def _spotify_track_item(track_id: str = "trk1") -> dict:
    return {
        "id": track_id,
        "name": "A Great Song",
        "artists": [{"name": "Some Artist"}],
        "album": {
            "name": "Some Album",
            "images": [{"url": "https://example.internal/art.jpg"}],
            "release_date": "2021-05-01",
        },
        "duration_ms": 210_000,
    }


class TestFetchSavedTracks:
    async def test_normalizes_saved_tracks(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(re.escape(spotify_oauth.SPOTIFY_SAVED_TRACKS_URL) + r".*"),
            json={"items": [{"track": _spotify_track_item()}]},
        )
        results = await spotify_oauth.fetch_saved_tracks("at-1", http=SafeHttpClient())
        assert len(results) == 1
        track = results[0]
        assert track.source == "spotify"
        assert track.source_id == "trk1"
        assert track.title == "A Great Song"
        assert track.artist == "Some Artist"
        assert track.album == "Some Album"
        assert track.duration_seconds == 210
        assert track.year == 2021
        assert track.artwork_url == "https://example.internal/art.jpg"

    async def test_skips_items_with_no_track(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(re.escape(spotify_oauth.SPOTIFY_SAVED_TRACKS_URL) + r".*"),
            json={"items": [{"track": None}, {"track": _spotify_track_item("trk2")}]},
        )
        results = await spotify_oauth.fetch_saved_tracks("at-1", http=SafeHttpClient())
        assert len(results) == 1
        assert results[0].source_id == "trk2"

    async def test_raises_on_http_failure(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(re.escape(spotify_oauth.SPOTIFY_SAVED_TRACKS_URL) + r".*"),
            status_code=401,
        )
        with pytest.raises(spotify_oauth.SpotifyOAuthError):
            await spotify_oauth.fetch_saved_tracks("bad-token", http=SafeHttpClient())


class TestFetchPlaylists:
    async def test_normalizes_playlists(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(re.escape(spotify_oauth.SPOTIFY_PLAYLISTS_URL) + r".*"),
            json={
                "items": [
                    {"id": "pl1", "name": "Roadtrip", "tracks": {"total": 42}},
                    {"id": "pl2", "name": None, "tracks": {"total": 0}},
                ]
            },
        )
        playlists = await spotify_oauth.fetch_playlists("at-1", http=SafeHttpClient())
        assert len(playlists) == 2
        assert playlists[0].id == "pl1"
        assert playlists[0].track_count == 42
        assert playlists[1].name == "Untitled playlist"

    async def test_raises_on_http_failure(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(re.escape(spotify_oauth.SPOTIFY_PLAYLISTS_URL) + r".*"),
            status_code=500,
        )
        with pytest.raises(spotify_oauth.SpotifyOAuthError):
            await spotify_oauth.fetch_playlists("at-1", http=SafeHttpClient())


class TestFetchPlaylistTracks:
    async def test_normalizes_playlist_tracks(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(r"https://api\.spotify\.com/v1/playlists/pl1/tracks.*"),
            json={"items": [{"track": _spotify_track_item("trk3")}]},
        )
        results = await spotify_oauth.fetch_playlist_tracks(
            "pl1", "at-1", http=SafeHttpClient()
        )
        assert len(results) == 1
        assert results[0].source_id == "trk3"

    async def test_raises_on_http_failure(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(r"https://api\.spotify\.com/v1/playlists/pl1/tracks.*"),
            status_code=404,
        )
        with pytest.raises(spotify_oauth.SpotifyOAuthError):
            await spotify_oauth.fetch_playlist_tracks(
                "pl1", "at-1", http=SafeHttpClient()
            )
