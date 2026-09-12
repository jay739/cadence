"""A small, deliberately isolated HTTP wrapper around yt-dlp.

Exists as its own process for one reason: yt-dlp's YouTube extractor breaks every time
YouTube changes their player, sometimes for hours until an upstream fix lands. Running it as a
subprocess inside Cadence's own FastAPI process would mean a broken extractor can wedge or
crash the whole app; running it here means the worst case is this one small service returning
errors while search, requests, and every other fulfillment tier keep working.

No YouTube account is ever used, anonymous yt-dlp extraction only. Signing a real Google
account into anything that shells out to yt-dlp this often is the actual account-ban risk;
anonymous extraction has no account to lose.

This service is honest about what it can deliver: every download job carries the requester's
quality ask, and a "lossless" one is rejected outright rather than answered with the Opus/AAC
audio YouTube actually serves. There is no lossless YouTube stream to find, so the only useful
answer is a clear refusal that lets the caller keep looking elsewhere.

API shape mirrors Cadence's own slskd client deliberately (create a job, poll it), so the
fulfillment code that talks to this tier reads the same way as the one that talks to slskd:
POST /search runs synchronously (a search is quick), POST /downloads starts a background job
and returns immediately, GET /downloads/{id} is polled until it reports done.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

DOWNLOADS_DIR = Path("/downloads")
YT_DLP_TIMEOUT_SECONDS = 60
DOWNLOAD_TIMEOUT_SECONDS = 300

# A bare 11-character YouTube video id, the only shape ever accepted into a yt-dlp command
# line here. Rejects anything else outright rather than trying to sanitize it, since this
# value ends up in a subprocess argument list and a filename template.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

app = FastAPI(title="ytdlp-shim")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=25)


class SearchResult(BaseModel):
    video_id: str
    title: str
    uploader: str | None
    duration_seconds: int | None


# What a caller can ask this service for. There is no "lossless" *option* here, only a
# "lossless" ask this service will refuse: YouTube serves Opus and AAC and nothing else, so a
# lossless job is not a job that takes longer or costs more, it is one that cannot be done.
# Accepting it and handing back an Opus file is the bug this validation exists to make
# impossible (see the 422 in start_download).
ACCEPTED_QUALITIES = ("any", "lossy", "lossless")
LOSSLESS_QUALITY = "lossless"


class DownloadRequest(BaseModel):
    video_id: str = Field(min_length=11, max_length=11)
    # Defaults to "any" so a caller that predates this field (or genuinely does not care) keeps
    # the exact previous behaviour: best available audio, no re-encode.
    quality: str = Field(default="any")

    @field_validator("quality")
    @classmethod
    def _known_quality(cls, v: str) -> str:
        if v not in ACCEPTED_QUALITIES:
            raise ValueError(f"quality must be one of {list(ACCEPTED_QUALITIES)}")
        return v


class DownloadStatus(BaseModel):
    job_id: str
    state: str  # "running" | "completed" | "failed"
    filename: str | None = None
    error: str | None = None


@dataclass
class _Job:
    state: str = "running"
    filename: str | None = None
    error: str | None = None
    process: asyncio.subprocess.Process | None = field(default=None, repr=False)


# In-memory only, deliberately: a job lost to a restart mid-download is indistinguishable from
# any other failed attempt to the caller, which already has its own retry/attempt-budget logic
# (mirroring how a killed torrent or a dropped Soulseek transfer is handled). Not worth a
# database for a fulfillment tier whose own upstream (YouTube) is itself unreliable.
_jobs: dict[str, _Job] = {}

# Strong references to every running background task, required for correctness, not just
# tidiness: asyncio only guarantees a task keeps running for as long as something holds a
# reference to it. A bare "fire and forget" task with nothing anchoring it can be garbage
# collected mid-run, and under Starlette's TestClient specifically it can also be swept up
# when the originating request's own async scope closes. Confirmed the hard way: the exact
# same job logic ran to completion correctly called directly, but silently never progressed
# past "running" when started this way from inside a route handler without this set.
_background_tasks: set[asyncio.Task] = set()


def _run_yt_dlp_sync(args: list[str], *, timeout: int) -> str:
    """Run yt-dlp and return stdout, raising CalledProcessError/TimeoutExpired on failure.

    Synchronous on purpose: search is expected to finish in a few seconds and callers already
    treat this endpoint as blocking, matching how quickly the other search sources answer.
    """
    result = subprocess.run(
        ["yt-dlp", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    return result.stdout


@app.get("/health")
def health() -> dict:
    try:
        version = subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except Exception as exc:  # noqa: BLE001 - health check must never itself raise
        return {"status": "error", "detail": str(exc)}
    return {"status": "ok", "yt_dlp_version": version}


@app.post("/search", response_model=list[SearchResult])
def search(body: SearchRequest) -> list[SearchResult]:
    # ytsearchN: is yt-dlp's own search-and-list syntax; --flat-playlist skips resolving each
    # result to its full format list, which is the slow part we don't need just to search.
    query = f"ytsearch{body.limit}:{body.query}"
    try:
        stdout = _run_yt_dlp_sync(
            [
                "--dump-json",
                "--flat-playlist",
                "--no-warnings",
                "--skip-download",
                query,
            ],
            timeout=YT_DLP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="yt-dlp search timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=502, detail=f"yt-dlp search failed: {exc.stderr[:500]}"
        ) from exc

    results: list[SearchResult] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        video_id = entry.get("id")
        if not video_id or not _VIDEO_ID_RE.match(video_id):
            continue
        results.append(
            SearchResult(
                video_id=video_id,
                title=entry.get("title") or "",
                uploader=entry.get("uploader") or entry.get("channel"),
                duration_seconds=entry.get("duration"),
            )
        )
    return results


async def _run_download_job(job_id: str, video_id: str) -> None:
    """Extract the best available audio for one video.

    Only ever reached for a "lossy" or "any" job -- a "lossless" one is refused at the route,
    before any job exists. `--audio-format best` is deliberately a passthrough rather than a
    conversion: it keeps whatever codec YouTube served (Opus, usually) instead of re-encoding
    it into something that would be lossier still, or into a lossless container that would
    misrepresent lossy audio as lossless.
    """
    job = _jobs[job_id]
    output_template = str(DOWNLOADS_DIR / f"{video_id}.%(ext)s")
    try:
        process = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--no-warnings",
            "-x",
            "--audio-format",
            "best",
            "--audio-quality",
            "0",
            "-o",
            output_template,
            "--",
            video_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        job.process = process
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=DOWNLOAD_TIMEOUT_SECONDS
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            job.state = "failed"
            job.error = f"download exceeded {DOWNLOAD_TIMEOUT_SECONDS}s"
            return

        if process.returncode != 0:
            job.state = "failed"
            job.error = stderr.decode(errors="replace")[:1000]
            return

        matches = sorted(DOWNLOADS_DIR.glob(f"{video_id}.*"))
        if not matches:
            job.state = "failed"
            job.error = "yt-dlp exited cleanly but produced no output file"
            return

        job.filename = matches[0].name
        job.state = "completed"
    except (
        Exception
    ) as exc:  # noqa: BLE001 - a job must report failure, never crash the loop
        job.state = "failed"
        job.error = str(exc)[:1000]


@app.post("/downloads", response_model=DownloadStatus, status_code=202)
async def start_download(body: DownloadRequest) -> DownloadStatus:
    if not _VIDEO_ID_RE.match(body.video_id):
        raise HTTPException(
            status_code=422, detail="video_id must be an 11-character id"
        )
    if body.quality == LOSSLESS_QUALITY:
        # Refused, not downgraded and not silently honoured. yt-dlp can happily be pointed at
        # a YouTube stream and told to write a .flac, but that only wraps already-lossy audio
        # in a lossless container -- a file that lies about what it is, which is worse for the
        # requester than a plain "this tier cannot do that".
        raise HTTPException(
            status_code=422,
            detail=(
                "YouTube has no lossless source: every stream it serves is Opus or AAC. "
                "Request 'lossy' or 'any' from this service, or fulfil the lossless request "
                "from a source that actually has lossless audio."
            ),
        )
    job_id = str(uuid.uuid4())
    _jobs[job_id] = _Job()
    task = asyncio.ensure_future(_run_download_job(job_id, body.video_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return DownloadStatus(job_id=job_id, state="running")


@app.get("/downloads/{job_id}", response_model=DownloadStatus)
def get_download(job_id: str) -> DownloadStatus:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job id")
    return DownloadStatus(
        job_id=job_id, state=job.state, filename=job.filename, error=job.error
    )
