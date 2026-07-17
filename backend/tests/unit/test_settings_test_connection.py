"""Unit tests for the per-integration "test connection" checks.

Each check is mocked at the HTTP layer (pytest-httpx) so these are provably correct without any
live Prowlarr/qBittorrent/slskd/Spotify instance, matching the mission's requirement that a test
connection makes exactly one real outbound call through the SSRF-hardened SafeHttpClient.
"""

from __future__ import annotations

import re

import pytest

from app.services.settings_test_connection import (
    test_prowlarr as check_prowlarr,
)
from app.services.settings_test_connection import (
    test_qbittorrent as check_qbittorrent,
)
from app.services.settings_test_connection import (
    test_slskd as check_slskd,
)
from app.services.settings_test_connection import (
    test_spotify as check_spotify,
)

pytestmark = pytest.mark.asyncio


class TestProwlarrConnection:
    async def test_missing_fields_fails_without_network_call(self):
        ok, detail = await check_prowlarr(None, None)
        assert ok is False
        assert "required" in detail

    async def test_success(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(r"http://localhost:19700/api/v1/system/status.*"),
            json={"version": "1.0"},
        )
        ok, detail = await check_prowlarr("http://localhost:19700", "a-key")
        assert ok is True
        assert "successfully" in detail

    async def test_auth_failure(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(r"http://localhost:19701/api/v1/system/status.*"),
            status_code=401,
        )
        ok, detail = await check_prowlarr("http://localhost:19701", "wrong-key")
        assert ok is False
        assert "rejected" in detail

    async def test_server_error(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(r"http://localhost:19702/api/v1/system/status.*"),
            status_code=500,
        )
        ok, detail = await check_prowlarr("http://localhost:19702", "a-key")
        assert ok is False
        assert "500" in detail

    async def test_unreachable_host_reports_failure_not_exception(self):
        # A bogus, non-resolvable hostname is blocked by SafeHttpClient's DNS check itself; the
        # test-connection wrapper must turn that into a reported failure, never propagate a raw
        # exception up through the admin route.
        ok, detail = await check_prowlarr(
            "http://this-host-does-not-exist.invalid", "a-key"
        )
        assert ok is False
        assert "Could not reach" in detail


class TestQbittorrentConnection:
    async def test_missing_fields_fails_without_network_call(self):
        ok, detail = await check_qbittorrent(None, None, None)
        assert ok is False
        assert "required" in detail

    async def test_success(self, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:19703/api/v2/auth/login",
            method="POST",
            text="Ok.",
        )
        ok, detail = await check_qbittorrent("http://localhost:19703", "admin", "pass")
        assert ok is True

    async def test_wrong_credentials(self, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:19704/api/v2/auth/login",
            method="POST",
            text="Fails.",
        )
        ok, detail = await check_qbittorrent("http://localhost:19704", "admin", "wrong")
        assert ok is False
        assert "rejected" in detail


class TestSlskdConnection:
    async def test_missing_fields_fails_without_network_call(self):
        ok, detail = await check_slskd(None, None)
        assert ok is False
        assert "required" in detail

    async def test_success(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(r"http://localhost:19705/api/v0/session.*"),
            json={"connected": True},
        )
        ok, detail = await check_slskd("http://localhost:19705", "a-key")
        assert ok is True

    async def test_auth_failure(self, httpx_mock):
        httpx_mock.add_response(
            url=re.compile(r"http://localhost:19706/api/v0/session.*"), status_code=401
        )
        ok, detail = await check_slskd("http://localhost:19706", "wrong-key")
        assert ok is False
        assert "rejected" in detail


class TestSpotifyConnection:
    async def test_missing_fields_fails_without_network_call(self):
        ok, detail = await check_spotify(None, None)
        assert ok is False
        assert "required" in detail

    async def test_success(self, httpx_mock):
        httpx_mock.add_response(
            url="https://accounts.spotify.com/api/token",
            method="POST",
            json={"access_token": "tok", "expires_in": 3600},
        )
        ok, detail = await check_spotify("client-id", "client-secret")
        assert ok is True

    async def test_bad_credentials(self, httpx_mock):
        httpx_mock.add_response(
            url="https://accounts.spotify.com/api/token",
            method="POST",
            status_code=400,
        )
        ok, detail = await check_spotify("bad-id", "bad-secret")
        assert ok is False
        assert "rejected" in detail
