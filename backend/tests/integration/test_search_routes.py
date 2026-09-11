from __future__ import annotations

import json

import pytest

from tests.integration.conftest import make_admin_headers, register_and_login

pytestmark = pytest.mark.asyncio


def _parse_sse_source_events(body: str) -> list[dict]:
    """Parses `data: {...}\n\n`-style SSE frames back into SourceSearchResult payloads, in
    order, skipping the final `event: done` frame's empty `data: {}` (which has no `source`
    key and isn't one of the four search results).
    """
    events = []
    for block in body.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                payload = json.loads(line.removeprefix("data: "))
                if "source" in payload:
                    events.append(payload)
    return events


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


class TestSearchStream:
    async def test_requires_authentication(self, app_client):
        resp = await app_client.get("/api/search/stream", params={"q": "test"})
        assert resp.status_code == 401

    async def test_reachable_via_session_cookie_not_only_bearer_token(
        self, demo_app_client
    ):
        """The browser's EventSource API cannot set an Authorization header, so this route
        must authenticate off the session cookie login already sets, not only the Bearer
        token every other request uses.

        The cookie is set with Secure=True (correct for production, which always sits behind
        HTTPS per the mission), so the test transport's plain http://testserver origin won't
        actually resend it via its own cookie jar the way a real browser would over HTTPS.
        The cookie's value is extracted from the Set-Cookie response instead and attached
        directly, which isolates exactly what this test is for (the route accepts the cookie,
        with zero Authorization header present) from the unrelated question of whether a
        Secure cookie survives a plaintext-HTTP round trip.
        """
        resp = await demo_app_client.post(
            "/api/auth/register",
            json={
                "email": "cookiestream@example.com",
                "password": "a-strong-password-1",
            },
        )
        assert resp.status_code == 201
        session_cookie = resp.cookies.get("cadence_session")
        assert session_cookie is not None

        # No Authorization header at all: relies purely on the cookie, matching what a real
        # browser's EventSource request would carry.
        stream_resp = await demo_app_client.get(
            "/api/search/stream",
            params={"q": "Tum Hi Ho"},
            cookies={"cadence_session": session_cookie},
        )
        assert stream_resp.status_code == 200

    async def test_streams_all_four_sources_and_a_final_done_event(
        self, demo_app_client
    ):
        headers = await register_and_login(
            demo_app_client, "streamtest@example.com", "a-strong-password-1"
        )
        resp = await demo_app_client.get(
            "/api/search/stream", params={"q": "Tum Hi Ho"}, headers=headers
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse_source_events(resp.text)
        source_names = {e["source"] for e in events}
        assert source_names == {"apple", "musicbrainz", "spotify", "prowlarr"}
        assert all(e["ok"] for e in events)
        assert "event: done" in resp.text

    async def test_validates_query_length(self, demo_app_client):
        headers = await register_and_login(
            demo_app_client, "streamlentest@example.com", "a-strong-password-1"
        )
        resp = await demo_app_client.get(
            "/api/search/stream", params={"q": ""}, headers=headers
        )
        assert resp.status_code == 422

    async def test_rate_limited_after_threshold(self, demo_app_client):
        """The stream endpoint's budget (60/min) is deliberately double the batch endpoint's,
        since search-as-you-type fires several debounced requests per typed query; 65 calls
        must still trip it.
        """
        headers = await register_and_login(
            demo_app_client, "streamratelimit@example.com", "a-strong-password-1"
        )
        statuses = []
        for _ in range(65):
            resp = await demo_app_client.get(
                "/api/search/stream", params={"q": "test"}, headers=headers
            )
            statuses.append(resp.status_code)
        assert 429 in statuses
        # The batch budget (30/min) must NOT have leaked onto this route: the first 35
        # requests would already contain a 429 if it had.
        assert 429 not in statuses[:35]


class TestSearchStreamLiveMode:
    async def test_a_fast_source_is_not_blocked_by_a_slow_one(
        self, app_client, httpx_mock, monkeypatch
    ):
        """Proves the actual point of streaming: Apple (fast, no rate limit) must be yielded
        before MusicBrainz (hard rate-limited to 1 req/sec) even though MusicBrainz is
        registered second in the source list, since ordering follows completion time, not
        registration order.
        """
        import asyncio
        import re

        from app.search_sources import musicbrainz as mb_module

        # Force MusicBrainz's normally-1-second rate limiter to sleep long enough that Apple's
        # near-instant mocked response is guaranteed to finish first.
        async def _slow_wait(self):
            await asyncio.sleep(0.3)

        monkeypatch.setattr(mb_module._RateLimiter, "wait", _slow_wait)

        httpx_mock.add_response(
            url=re.compile(r"https://itunes\.apple\.com/search.*"),
            json={"results": []},
        )
        httpx_mock.add_response(
            url=re.compile(r"https://musicbrainz\.org/ws/2/recording.*"),
            json={"recordings": []},
        )

        headers = await register_and_login(
            app_client, "streamordertest@example.com", "a-strong-password-1"
        )
        resp = await app_client.get(
            "/api/search/stream", params={"q": "test query"}, headers=headers
        )
        assert resp.status_code == 200
        events = _parse_sse_source_events(resp.text)
        source_order = [e["source"] for e in events]
        assert source_order.index("apple") < source_order.index("musicbrainz")


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


class TestResolveLinkAuth:
    async def test_requires_authentication(self, app_client):
        resp = await app_client.post(
            "/api/search/resolve-link",
            json={"url": "https://open.spotify.com/track/6y0igZArWVi6Iz0rj35c1Y"},
        )
        assert resp.status_code == 401


class TestResolveLinkValidation:
    async def test_unrecognized_url_is_rejected_with_specific_message(
        self, demo_app_client
    ):
        headers = await register_and_login(
            demo_app_client, "resolvebadhost@example.com", "a-strong-password-1"
        )
        resp = await demo_app_client.post(
            "/api/search/resolve-link",
            json={"url": "https://youtube.com/watch?v=dQw4w9WgXcQ"},
            headers=headers,
        )
        assert resp.status_code == 422
        assert "Spotify or Apple Music" in resp.json()["detail"]

    async def test_malformed_url_is_rejected(self, demo_app_client):
        headers = await register_and_login(
            demo_app_client, "resolvemalformed@example.com", "a-strong-password-1"
        )
        resp = await demo_app_client.post(
            "/api/search/resolve-link",
            json={"url": "not a url at all"},
            headers=headers,
        )
        assert resp.status_code == 422

    async def test_empty_url_is_rejected_by_schema_validation(self, demo_app_client):
        headers = await register_and_login(
            demo_app_client, "resolveempty@example.com", "a-strong-password-1"
        )
        resp = await demo_app_client.post(
            "/api/search/resolve-link", json={"url": ""}, headers=headers
        )
        assert resp.status_code == 422

    async def test_spotify_non_track_link_is_rejected_with_specific_message(
        self, demo_app_client
    ):
        headers = await register_and_login(
            demo_app_client, "resolveplaylist@example.com", "a-strong-password-1"
        )
        resp = await demo_app_client.post(
            "/api/search/resolve-link",
            json={"url": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"},
            headers=headers,
        )
        assert resp.status_code == 422
        assert "Spotify" in resp.json()["detail"]


class TestResolveLinkDemoMode:
    async def test_valid_spotify_track_url_resolves(self, demo_app_client):
        headers = await register_and_login(
            demo_app_client, "resolvespotifydemo@example.com", "a-strong-password-1"
        )
        resp = await demo_app_client.post(
            "/api/search/resolve-link",
            json={"url": "https://open.spotify.com/track/6y0igZArWVi6Iz0rj35c1Y"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source"] == "spotify"
        assert body["source_id"] == "6y0igZArWVi6Iz0rj35c1Y"
        assert body["title"]
        assert body["artist"]

    async def test_valid_apple_music_song_link_resolves(self, demo_app_client):
        headers = await register_and_login(
            demo_app_client, "resolveappledemo@example.com", "a-strong-password-1"
        )
        resp = await demo_app_client.post(
            "/api/search/resolve-link",
            json={"url": "https://music.apple.com/us/song/tum-hi-ho/643421652"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source"] == "apple"
        assert body["source_id"] == "643421652"

    async def test_valid_apple_music_album_link_with_track_param_resolves(
        self, demo_app_client
    ):
        headers = await register_and_login(
            demo_app_client, "resolveapplealbumdemo@example.com", "a-strong-password-1"
        )
        resp = await demo_app_client.post(
            "/api/search/resolve-link",
            json={
                "url": "https://music.apple.com/in/album/aashiqui-2/643421641?i=643421652"
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["source_id"] == "643421652"

    async def test_same_link_resolves_to_the_same_fake_track_twice(
        self, demo_app_client
    ):
        headers = await register_and_login(
            demo_app_client, "resolvestabledemo@example.com", "a-strong-password-1"
        )
        payload = {"url": "https://open.spotify.com/track/6y0igZArWVi6Iz0rj35c1Y"}
        first = await demo_app_client.post(
            "/api/search/resolve-link", json=payload, headers=headers
        )
        second = await demo_app_client.post(
            "/api/search/resolve-link", json=payload, headers=headers
        )
        assert first.json() == second.json()


class TestResolveLinkLiveMode:
    async def test_valid_apple_music_link_resolves(self, app_client, httpx_mock):
        headers = await register_and_login(
            app_client, "resolveapplelive@example.com", "a-strong-password-1"
        )
        httpx_mock.add_response(
            url="https://itunes.apple.com/lookup?id=643421652&entity=song",
            json={
                "results": [
                    {
                        "trackId": 643421652,
                        "trackName": "Tum Hi Ho",
                        "artistName": "Arijit Singh",
                        "collectionName": "Aashiqui 2",
                        "trackTimeMillis": 262000,
                        "artworkUrl100": "https://example.test/100x100bb.jpg",
                        "releaseDate": "2013-04-26T00:00:00Z",
                    }
                ]
            },
        )
        resp = await app_client.post(
            "/api/search/resolve-link",
            json={"url": "https://music.apple.com/us/song/tum-hi-ho/643421652"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source"] == "apple"
        assert body["source_id"] == "643421652"
        assert body["title"] == "Tum Hi Ho"
        assert body["artist"] == "Arijit Singh"

    async def test_apple_track_not_found_returns_specific_404(
        self, app_client, httpx_mock
    ):
        headers = await register_and_login(
            app_client, "resolveapplenotfound@example.com", "a-strong-password-1"
        )
        httpx_mock.add_response(json={"resultCount": 0, "results": []})
        resp = await app_client.post(
            "/api/search/resolve-link",
            json={"url": "https://music.apple.com/us/song/does-not-exist/000000000"},
            headers=headers,
        )
        assert resp.status_code == 404
        assert "Apple Music" in resp.json()["detail"]

    async def test_valid_spotify_track_url_resolves_when_configured(
        self, app_client, httpx_mock
    ):
        admin_headers = await make_admin_headers(app_client)
        await _configure_spotify(app_client, admin_headers)
        headers = await register_and_login(
            app_client, "resolvespotifylive@example.com", "a-strong-password-1"
        )

        httpx_mock.add_response(
            url="https://accounts.spotify.com/api/token",
            json={"access_token": "tok123", "expires_in": 3600},
        )
        httpx_mock.add_response(
            url="https://api.spotify.com/v1/tracks?ids=6y0igZArWVi6Iz0rj35c1Y",
            json={
                "tracks": [
                    {
                        "id": "6y0igZArWVi6Iz0rj35c1Y",
                        "name": "Tum Hi Ho",
                        "artists": [{"name": "Arijit Singh"}],
                        "album": {
                            "name": "Aashiqui 2",
                            "images": [{"url": "https://example.test/art.jpg"}],
                            "release_date": "2013-04-26",
                        },
                        "duration_ms": 262000,
                    }
                ]
            },
        )
        resp = await app_client.post(
            "/api/search/resolve-link",
            json={"url": "https://open.spotify.com/track/6y0igZArWVi6Iz0rj35c1Y"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source"] == "spotify"
        assert body["title"] == "Tum Hi Ho"

    async def test_spotify_link_without_operator_config_returns_specific_400(
        self, app_client
    ):
        headers = await register_and_login(
            app_client, "resolvespotifyunconfigured@example.com", "a-strong-password-1"
        )
        resp = await app_client.post(
            "/api/search/resolve-link",
            json={"url": "https://open.spotify.com/track/6y0igZArWVi6Iz0rj35c1Y"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert "Spotify" in resp.json()["detail"]
