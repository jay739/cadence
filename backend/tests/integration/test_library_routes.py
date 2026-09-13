"""The /api/library/check endpoint end-to-end, including every way it is allowed to fail.

The contract this pins down is narrow on purpose: the endpoint may report `available: false`
and it may report fewer matches than really exist, but it must never return an error status for
an integration problem, and it must never claim a hit it cannot justify. Search is exercised
alongside it to show the two are genuinely independent.
"""

from __future__ import annotations

import re

import httpx
import pytest

from app.core.config import get_settings
from tests.integration.conftest import make_admin_headers, register_and_login

pytestmark = pytest.mark.asyncio

SEARCH3 = re.compile(r"http://localhost:4533/rest/search3\.view.*")


def _configure_navidrome(monkeypatch, *, with_credentials: bool = True) -> None:
    monkeypatch.setenv("NAVIDROME_URL", "http://localhost:4533")
    if with_credentials:
        monkeypatch.setenv("NAVIDROME_ADMIN_USER", "service")
        monkeypatch.setenv("NAVIDROME_ADMIN_PASSWORD", "service-password")
    get_settings.cache_clear()


def _hit(title: str, artist: str, song_id: str = "nd-1"):
    return {
        "subsonic-response": {
            "status": "ok",
            "searchResult3": {
                "song": [
                    {"id": song_id, "title": title, "artist": artist, "album": "Album"}
                ]
            },
        }
    }


MISS = {"subsonic-response": {"status": "ok", "searchResult3": {}}}

ONE_TRACK = {"tracks": [{"key": "spotify:1", "title": "One Love", "artist": "Blue"}]}


async def _headers(client, email="libtest@example.com"):
    return await register_and_login(client, email, "a-strong-password-1")


class TestAuth:
    async def test_requires_authentication(self, app_client):
        resp = await app_client.post("/api/library/check", json=ONE_TRACK)
        assert resp.status_code == 401


class TestConfigured:
    async def test_reports_a_hit(self, app_client, monkeypatch, httpx_mock):
        _configure_navidrome(monkeypatch)
        httpx_mock.add_response(url=SEARCH3, json=_hit("One Love", "Blue", "nd-abc"))

        resp = await app_client.post(
            "/api/library/check", json=ONE_TRACK, headers=await _headers(app_client)
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert len(body["matches"]) == 1
        match = body["matches"][0]
        assert match["key"] == "spotify:1"
        assert match["navidrome_id"] == "nd-abc"
        assert match["title"] == "One Love"
        assert match["album"] == "Album"

    async def test_reports_a_miss_as_available_with_no_matches(
        self, app_client, monkeypatch, httpx_mock
    ):
        _configure_navidrome(monkeypatch)
        httpx_mock.add_response(url=SEARCH3, json=MISS)

        resp = await app_client.post(
            "/api/library/check", json=ONE_TRACK, headers=await _headers(app_client)
        )

        assert resp.status_code == 200
        assert resp.json() == {"available": True, "matches": []}

    async def test_does_not_claim_a_hit_for_a_different_artist(
        self, app_client, monkeypatch, httpx_mock
    ):
        # Same title, someone else's recording. Asserting a hit here would be the failure mode
        # the whole feature is meant to avoid.
        _configure_navidrome(monkeypatch)
        httpx_mock.add_response(url=SEARCH3, json=_hit("One Love", "Bob Marley"))

        resp = await app_client.post(
            "/api/library/check", json=ONE_TRACK, headers=await _headers(app_client)
        )

        assert resp.status_code == 200
        assert resp.json()["matches"] == []

    async def test_rejects_an_oversized_batch(self, app_client, monkeypatch):
        _configure_navidrome(monkeypatch)
        payload = {
            "tracks": [
                {"key": f"k{n}", "title": f"Title {n}", "artist": "Artist"}
                for n in range(51)
            ]
        }
        resp = await app_client.post(
            "/api/library/check", json=payload, headers=await _headers(app_client)
        )
        assert resp.status_code == 422


class TestDegradesGracefully:
    async def test_unconfigured_reports_unavailable_rather_than_failing(
        self, app_client
    ):
        # No NAVIDROME_* set at all: the app still boots and the endpoint still answers 200.
        resp = await app_client.post(
            "/api/library/check", json=ONE_TRACK, headers=await _headers(app_client)
        )
        assert resp.status_code == 200
        assert resp.json() == {"available": False, "matches": []}

    async def test_navidrome_url_without_credentials_reports_unavailable(
        self, app_client, monkeypatch
    ):
        # Navidrome is fine as a login backend; the library check just stays off.
        _configure_navidrome(monkeypatch, with_credentials=False)
        resp = await app_client.post(
            "/api/library/check", json=ONE_TRACK, headers=await _headers(app_client)
        )
        assert resp.status_code == 200
        assert resp.json()["available"] is False

    async def test_unreachable_navidrome_reports_unavailable(
        self, app_client, monkeypatch, httpx_mock
    ):
        _configure_navidrome(monkeypatch)
        httpx_mock.add_exception(httpx.ConnectError("refused"), url=SEARCH3)

        resp = await app_client.post(
            "/api/library/check", json=ONE_TRACK, headers=await _headers(app_client)
        )

        assert resp.status_code == 200
        assert resp.json() == {"available": False, "matches": []}

    async def test_navidrome_server_error_reports_unavailable(
        self, app_client, monkeypatch, httpx_mock
    ):
        _configure_navidrome(monkeypatch)
        httpx_mock.add_response(url=SEARCH3, status_code=500)

        resp = await app_client.post(
            "/api/library/check", json=ONE_TRACK, headers=await _headers(app_client)
        )

        assert resp.status_code == 200
        assert resp.json()["available"] is False

    async def test_demo_mode_reports_unavailable_rather_than_inventing_hits(
        self, demo_app_client
    ):
        resp = await demo_app_client.post(
            "/api/library/check",
            json=ONE_TRACK,
            headers=await _headers(demo_app_client, "demolib@example.com"),
        )
        assert resp.status_code == 200
        assert resp.json() == {"available": False, "matches": []}


class TestNeverBreaksSearch:
    @pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
    async def test_search_survives_navidrome_and_every_source_being_down(
        self, app_client, monkeypatch, httpx_mock
    ):
        """With Navidrome dead and every search source dead, search still answers.

        The catch-all exception below stands in for a total outbound-network failure, which
        also keeps the test hermetic -- nothing here reaches the real Apple or MusicBrainz.
        Search is expected to report per-source failures, not to return an error itself, and
        the library check is expected to report unavailable rather than propagate anything
        into that path.
        """
        _configure_navidrome(monkeypatch)
        httpx_mock.add_exception(httpx.ConnectError("refused"), url=SEARCH3)
        httpx_mock.add_exception(httpx.ConnectError("refused"), is_reusable=True)
        headers = await _headers(app_client, "searchsafe@example.com")

        check = await app_client.post(
            "/api/library/check", json=ONE_TRACK, headers=headers
        )
        search = await app_client.get(
            "/api/search", params={"q": "anything"}, headers=headers
        )

        assert check.status_code == 200
        assert check.json() == {"available": False, "matches": []}
        assert search.status_code == 200
        assert "sources" in search.json()

    async def test_demo_search_is_untouched_by_the_library_check(
        self, demo_app_client, monkeypatch
    ):
        # Demo mode gives search a deterministic, dependency-free result set, so this pins that
        # adding the library integration changed nothing about what search returns.
        _configure_navidrome(monkeypatch)
        headers = await _headers(demo_app_client, "searchsafe2@example.com")

        check = await demo_app_client.post(
            "/api/library/check", json=ONE_TRACK, headers=headers
        )
        search = await demo_app_client.get(
            "/api/search", params={"q": "Tum Hi Ho"}, headers=headers
        )

        assert check.json()["available"] is False
        assert search.status_code == 200
        assert {s["source"] for s in search.json()["sources"]} == {
            "apple",
            "musicbrainz",
            "spotify",
            "prowlarr",
        }
        assert all(s["ok"] for s in search.json()["sources"])


class TestHealthReporting:
    async def test_health_reports_the_library_check_as_unconfigured_by_default(
        self, app_client
    ):
        headers = await make_admin_headers(app_client)
        resp = await app_client.get("/api/health/integrations", headers=headers)
        assert resp.json()["integrations"]["navidrome_library"]["configured"] is False

    async def test_health_reports_configured_once_credentials_are_supplied(
        self, app_client, monkeypatch, httpx_mock
    ):
        _configure_navidrome(monkeypatch)
        headers = await make_admin_headers(app_client)
        # Health probes Navidrome twice: the anonymous ping for the login backend, then an
        # authenticated one for the service account. One reusable response covers both.
        httpx_mock.add_response(
            url=re.compile(r"http://localhost:4533/rest/.*"),
            json={"subsonic-response": {"status": "ok"}},
            is_reusable=True,
        )
        resp = await app_client.get("/api/health/integrations", headers=headers)
        assert resp.json()["integrations"]["navidrome_library"]["configured"] is True
