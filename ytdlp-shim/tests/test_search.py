"""Search endpoint: NDJSON parsing, malformed-line tolerance, video id validation.

subprocess.run is monkeypatched everywhere here; nothing in this file shells out to real
yt-dlp or touches the network.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

VALID_ID_A = "dQw4w9WgXcQ"
VALID_ID_B = "aBcDeFgHiJk"


def _ndjson(*entries: dict) -> str:
    import json

    return "\n".join(json.dumps(e) for e in entries)


def _fake_completed(stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["yt-dlp"], returncode=0, stdout=stdout, stderr=""
    )


class TestSearch:
    def test_parses_valid_results(self, client):
        stdout = _ndjson(
            {
                "id": VALID_ID_A,
                "title": "Daft Punk - Get Lucky",
                "uploader": "Daft Punk",
                "duration": 369,
            },
            {"id": VALID_ID_B, "title": "Get Lucky (Live)", "channel": "SomeChannel"},
        )
        with patch("subprocess.run", return_value=_fake_completed(stdout)):
            resp = client.post("/search", json={"query": "get lucky", "limit": 5})

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        assert body[0] == {
            "video_id": VALID_ID_A,
            "title": "Daft Punk - Get Lucky",
            "uploader": "Daft Punk",
            "duration_seconds": 369,
        }
        # Falls back to "channel" when "uploader" is absent.
        assert body[1]["uploader"] == "SomeChannel"

    def test_skips_malformed_json_lines_without_failing_the_whole_request(self, client):
        stdout = "\n".join(
            [
                '{"id": "' + VALID_ID_A + '", "title": "Good Entry"}',
                "not json at all",
                "",
                '{"id": "' + VALID_ID_B + '", "title": "Also Good"}',
            ]
        )
        with patch("subprocess.run", return_value=_fake_completed(stdout)):
            resp = client.post("/search", json={"query": "x"})

        assert resp.status_code == 200
        ids = {r["video_id"] for r in resp.json()}
        assert ids == {VALID_ID_A, VALID_ID_B}

    def test_drops_entries_with_a_malformed_video_id(self, client):
        # yt-dlp can return non-video entries (channels, playlists) from a search in edge
        # cases; anything whose "id" isn't a real 11-character video id must never reach a
        # later yt-dlp download command built from it.
        stdout = _ndjson(
            {"id": "not-a-video-id-at-all", "title": "Should be dropped"},
            {"id": VALID_ID_A, "title": "Should survive"},
        )
        with patch("subprocess.run", return_value=_fake_completed(stdout)):
            resp = client.post("/search", json={"query": "x"})

        ids = [r["video_id"] for r in resp.json()]
        assert ids == [VALID_ID_A]

    def test_yt_dlp_failure_returns_502_not_a_crash(self, client):
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(
                1, ["yt-dlp"], stderr="ERROR: no results found"
            ),
        ):
            resp = client.post("/search", json={"query": "x"})
        assert resp.status_code == 502

    def test_yt_dlp_timeout_returns_504_not_a_hang(self, client):
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["yt-dlp"], timeout=60),
        ):
            resp = client.post("/search", json={"query": "x"})
        assert resp.status_code == 504

    def test_rejects_empty_query(self, client):
        resp = client.post("/search", json={"query": ""})
        assert resp.status_code == 422

    def test_rejects_limit_out_of_range(self, client):
        resp = client.post("/search", json={"query": "x", "limit": 100})
        assert resp.status_code == 422
