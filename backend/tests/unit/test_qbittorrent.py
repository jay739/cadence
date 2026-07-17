from __future__ import annotations

import pytest

from app.services.qbittorrent import QBittorrentClient


class TestQBittorrentConfigured:
    def test_not_configured_when_missing_fields(self):
        assert QBittorrentClient(None, None, None).configured is False
        assert (
            QBittorrentClient("http://localhost:8080", "user", None).configured is False
        )

    def test_configured_when_all_fields_present(self):
        client = QBittorrentClient("http://localhost:8080", "user", "pass")
        assert client.configured is True


@pytest.mark.asyncio
class TestQBittorrentLogin:
    async def test_login_success_sets_sid_cookie(self, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/auth/login",
            text="Ok.",
            headers={"Set-Cookie": "SID=abc123; Path=/"},
        )
        client = QBittorrentClient("http://localhost:8080", "user", "pass")
        result = await client._login()
        assert result is True
        assert client._sid_cookie == "abc123"

    async def test_login_failure_returns_false(self, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/auth/login", text="Fails."
        )
        client = QBittorrentClient("http://localhost:8080", "user", "wrongpass")
        result = await client._login()
        assert result is False

    async def test_not_configured_login_returns_false_without_request(self):
        client = QBittorrentClient(None, None, None)
        result = await client._login()
        assert result is False


@pytest.mark.asyncio
class TestQBittorrentFindTorrent:
    async def test_find_torrent_by_hint_matches_case_insensitively(self, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/auth/login",
            text="Ok.",
            headers={"Set-Cookie": "SID=abc123; Path=/"},
        )
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/torrents/info",
            json=[{"name": "Arijit Singh - Tum Hi Ho [FLAC]", "hash": "hash1"}],
        )
        client = QBittorrentClient("http://localhost:8080", "user", "pass")
        torrent = await client.find_torrent_by_hint("tum hi ho")
        assert torrent is not None
        assert torrent["hash"] == "hash1"

    async def test_find_torrent_by_hint_no_match_returns_none(self, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/auth/login",
            text="Ok.",
            headers={"Set-Cookie": "SID=abc123; Path=/"},
        )
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/torrents/info",
            json=[{"name": "Some Other Song", "hash": "hash2"}],
        )
        client = QBittorrentClient("http://localhost:8080", "user", "pass")
        torrent = await client.find_torrent_by_hint("tum hi ho")
        assert torrent is None

    async def test_not_configured_returns_none(self):
        client = QBittorrentClient(None, None, None)
        assert await client.find_torrent_by_hint("anything") is None


@pytest.mark.asyncio
class TestQBittorrentCompletion:
    async def test_is_complete_true_at_full_progress(self, httpx_mock):
        client = QBittorrentClient("http://localhost:8080", "user", "pass")
        client._sid_cookie = "abc123"
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/torrents/info?hashes=hash1",
            json=[{"progress": 1.0}],
        )
        assert await client.is_complete("hash1") is True

    async def test_is_complete_false_when_partial(self, httpx_mock):
        client = QBittorrentClient("http://localhost:8080", "user", "pass")
        client._sid_cookie = "abc123"
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/torrents/info?hashes=hash1",
            json=[{"progress": 0.5}],
        )
        assert await client.is_complete("hash1") is False

    async def test_is_complete_false_without_login(self):
        client = QBittorrentClient("http://localhost:8080", "user", "pass")
        assert await client.is_complete("hash1") is False

    async def test_get_content_path_picks_largest_file(self, httpx_mock):
        client = QBittorrentClient("http://localhost:8080", "user", "pass")
        client._sid_cookie = "abc123"
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/torrents/files?hash=hash1",
            json=[
                {"name": "cover.jpg", "size": 1000},
                {"name": "song.flac", "size": 30_000_000},
            ],
        )
        content = await client.get_content_path("hash1")
        assert content == "song.flac"

    async def test_get_content_path_empty_returns_none(self, httpx_mock):
        client = QBittorrentClient("http://localhost:8080", "user", "pass")
        client._sid_cookie = "abc123"
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/torrents/files?hash=hash1", json=[]
        )
        assert await client.get_content_path("hash1") is None
