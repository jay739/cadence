"""Download job lifecycle: create, poll, success, failure, timeout.

The subprocess layer is faked with a small stand-in that mimics asyncio.subprocess.Process's
interface (communicate/kill/wait/returncode), so nothing here spawns a real yt-dlp process.

Uses async_client (see conftest.py), not the synchronous TestClient: these tests exercise a
background task started inside the route handler, and that only progresses correctly on a
loop the test itself keeps running, confirmed directly while writing these.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

VALID_ID = "dQw4w9WgXcQ"


class _FakeProcess:
    def __init__(self, *, returncode: int, stderr: bytes = b"", hang: bool = False):
        self.returncode = returncode
        self._stderr = stderr
        self._hang = hang
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(
                3600
            )  # never actually reached; the caller times out first
        return (b"", self._stderr)

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


async def _await_job_settled(async_client, job_id: str, tries: int = 40):
    for _ in range(tries):
        resp = await async_client.get(f"/downloads/{job_id}")
        if resp.json()["state"] != "running":
            return resp
        await asyncio.sleep(0.02)
    pytest.fail("job never left the running state")


class TestDownloadLifecycle:
    async def test_successful_download_reports_completed_with_filename(
        self, async_client, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("app.main.DOWNLOADS_DIR", tmp_path)
        (tmp_path / f"{VALID_ID}.opus").write_bytes(b"fake audio")

        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_FakeProcess(returncode=0)),
        ):
            create = await async_client.post("/downloads", json={"video_id": VALID_ID})
            assert create.status_code == 202
            job_id = create.json()["job_id"]
            final = await _await_job_settled(async_client, job_id)

        assert final.json()["state"] == "completed"
        assert final.json()["filename"] == f"{VALID_ID}.opus"

    async def test_yt_dlp_nonzero_exit_reports_failed_with_stderr(
        self, async_client, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("app.main.DOWNLOADS_DIR", tmp_path)

        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(
                return_value=_FakeProcess(
                    returncode=1, stderr=b"ERROR: video unavailable"
                )
            ),
        ):
            create = await async_client.post("/downloads", json={"video_id": VALID_ID})
            job_id = create.json()["job_id"]
            final = await _await_job_settled(async_client, job_id)

        assert final.json()["state"] == "failed"
        assert "video unavailable" in final.json()["error"]

    async def test_clean_exit_with_no_output_file_still_reports_failed(
        self, async_client, tmp_path, monkeypatch
    ):
        # A real failure mode: yt-dlp exits 0 but, for whatever reason (a format that couldn't
        # be extracted, a race with cleanup), the expected file just isn't there. Must not be
        # reported as success just because the process exit code looked fine.
        monkeypatch.setattr("app.main.DOWNLOADS_DIR", tmp_path)

        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_FakeProcess(returncode=0)),
        ):
            create = await async_client.post("/downloads", json={"video_id": VALID_ID})
            job_id = create.json()["job_id"]
            final = await _await_job_settled(async_client, job_id)

        assert final.json()["state"] == "failed"
        assert "no output file" in final.json()["error"]

    async def test_download_timeout_kills_the_process_and_reports_failed(
        self, async_client, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("app.main.DOWNLOADS_DIR", tmp_path)
        monkeypatch.setattr("app.main.DOWNLOAD_TIMEOUT_SECONDS", 0.05)
        fake_process = _FakeProcess(returncode=0, hang=True)

        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_process)
        ):
            create = await async_client.post("/downloads", json={"video_id": VALID_ID})
            job_id = create.json()["job_id"]
            final = await _await_job_settled(async_client, job_id)

        assert final.json()["state"] == "failed"
        assert "exceeded" in final.json()["error"]
        assert fake_process.killed is True

    async def test_rejects_a_malformed_video_id_before_touching_the_subprocess_layer(
        self, async_client
    ):
        with patch("asyncio.create_subprocess_exec") as mocked:
            resp = await async_client.post(
                "/downloads", json={"video_id": "../../etc/passwd"}
            )
        assert resp.status_code == 422
        mocked.assert_not_called()

    async def test_polling_an_unknown_job_id_is_404_not_a_crash(self, async_client):
        resp = await async_client.get("/downloads/does-not-exist")
        assert resp.status_code == 404
