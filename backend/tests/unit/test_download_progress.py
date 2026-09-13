"""Per-tier download progress reporting.

The one rule these tests exist to hold: a percentage is reported only where the backend
actually measured one. qBittorrent and slskd both do (verified against a live slskd 0.26.0.0,
whose per-file transfer objects carry `percentComplete` alongside `bytesTransferred`/`size`),
and both are read off the very same payloads the completion checks already fetch. Anything
unreadable, missing or malformed must come back as None rather than 0.0, because "we don't
know" and "no bytes yet" are different facts and a UI cannot tell them apart once they have
been flattened into the same number.
"""

from __future__ import annotations

import pytest

from app.services.qbittorrent import QBittorrentClient
from app.services.slskd import SlskdClient


def _transfers_payload(**file_overrides) -> dict:
    """A slskd downloads payload shaped exactly like the live daemon's.

    Field names and nesting mirror a real response captured from slskd 0.26.0.0, so a rename
    upstream shows up here as a failure rather than as a silently absent percentage.
    """
    transfer = {
        "id": "bbe1243e-9593-4557-a228-b4435e11b4bf",
        "username": "peer",
        "direction": "Download",
        "filename": "music\\song.flac",
        "size": 1000,
        "state": "InProgress",
        "bytesTransferred": 250,
        "bytesRemaining": 750,
        "averageSpeed": 53502.99,
        "percentComplete": 25,
    }
    transfer.update(file_overrides)
    return {"directories": [{"directory": "music", "files": [transfer]}]}


@pytest.mark.asyncio
class TestQBittorrentProgress:
    async def test_reports_the_torrents_real_fraction_as_a_percentage(self, httpx_mock):
        client = QBittorrentClient("http://localhost:8080", "user", "pass")
        client._sid_cookie = "abc123"
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/torrents/info?hashes=hash1",
            json=[{"progress": 0.42}],
        )
        assert await client.progress_percent("hash1") == pytest.approx(42.0)

    async def test_reports_none_when_the_field_is_missing(self, httpx_mock):
        """Missing is not zero: a torrent qBittorrent has not scored yet has no percentage."""
        client = QBittorrentClient("http://localhost:8080", "user", "pass")
        client._sid_cookie = "abc123"
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/torrents/info?hashes=hash1",
            json=[{"name": "something"}],
        )
        assert await client.progress_percent("hash1") is None

    async def test_reports_none_for_an_unknown_torrent(self, httpx_mock):
        client = QBittorrentClient("http://localhost:8080", "user", "pass")
        client._sid_cookie = "abc123"
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/torrents/info?hashes=hash1", json=[]
        )
        assert await client.progress_percent("hash1") is None

    async def test_reports_none_without_a_session(self):
        client = QBittorrentClient("http://localhost:8080", "user", "pass")
        assert await client.progress_percent("hash1") is None

    async def test_clamps_a_completed_torrent_to_100(self, httpx_mock):
        client = QBittorrentClient("http://localhost:8080", "user", "pass")
        client._sid_cookie = "abc123"
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/torrents/info?hashes=hash1",
            json=[{"progress": 1.0}],
        )
        assert await client.progress_percent("hash1") == pytest.approx(100.0)


@pytest.mark.asyncio
class TestSlskdProgress:
    async def test_prefers_the_percent_complete_slskd_reports(self, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:5030/api/v0/transfers/downloads/peer",
            json=_transfers_payload(),
        )
        client = SlskdClient("http://localhost:5030", "key")
        assert await client.download_progress(
            "peer", "music\\song.flac"
        ) == pytest.approx(25.0)

    async def test_falls_back_to_byte_counts_when_percent_is_absent(self, httpx_mock):
        """An older daemon (or a transfer slskd has not scored) still has the byte counts."""
        payload = _transfers_payload()
        del payload["directories"][0]["files"][0]["percentComplete"]
        httpx_mock.add_response(
            url="http://localhost:5030/api/v0/transfers/downloads/peer", json=payload
        )
        client = SlskdClient("http://localhost:5030", "key")
        assert await client.download_progress(
            "peer", "music\\song.flac"
        ) == pytest.approx(25.0)

    async def test_reports_none_when_neither_percent_nor_sizes_are_usable(
        self, httpx_mock
    ):
        payload = _transfers_payload(size=0)
        del payload["directories"][0]["files"][0]["percentComplete"]
        httpx_mock.add_response(
            url="http://localhost:5030/api/v0/transfers/downloads/peer", json=payload
        )
        client = SlskdClient("http://localhost:5030", "key")
        assert await client.download_progress("peer", "music\\song.flac") is None

    async def test_reports_none_for_a_file_slskd_does_not_know_about(self, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:5030/api/v0/transfers/downloads/peer",
            json=_transfers_payload(),
        )
        client = SlskdClient("http://localhost:5030", "key")
        assert await client.download_progress("peer", "music\\other.flac") is None

    async def test_reports_none_when_not_configured(self):
        client = SlskdClient(None, None)
        assert await client.download_progress("peer", "music\\song.flac") is None

    async def test_completion_check_still_reads_the_same_payload(self, httpx_mock):
        """The refactor that added progress must not have changed what "complete" means."""
        httpx_mock.add_response(
            url="http://localhost:5030/api/v0/transfers/downloads/peer",
            json=_transfers_payload(state="Completed, Succeeded"),
        )
        client = SlskdClient("http://localhost:5030", "key")
        assert await client.is_download_complete("peer", "music\\song.flac") is True

    async def test_an_errored_transfer_is_not_complete(self, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:5030/api/v0/transfers/downloads/peer",
            json=_transfers_payload(state="Completed, Errored"),
        )
        client = SlskdClient("http://localhost:5030", "key")
        assert await client.is_download_complete("peer", "music\\song.flac") is False
