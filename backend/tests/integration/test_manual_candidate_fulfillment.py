"""Integration tests: a pinned candidate is what fulfillment actually downloads.

The route tests prove the pick is stored; these prove it is obeyed. Every external service is
mocked at the HTTP layer (pytest-httpx), so what the tier really sends is assertable rather
than inferred. The load-bearing assertions here are the ones about WHICH identifier was
requested: a test that only checked the request ended up fulfilled would pass just as happily
if fulfillment had re-scored and grabbed the wrong candidate again, which is the entire bug
this feature exists to fix.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.db.base import create_all, dispose_engine, get_sessionmaker, init_engine
from app.models.request import FulfillmentOutcome, Request, RequestStatus
from app.models.user import User, UserRole
from app.services.fulfillment import fulfill_request

pytestmark = pytest.mark.asyncio


async def _attempts_of(session, request_id: str) -> list:
    result = await session.execute(
        select(Request)
        .where(Request.id == request_id)
        .options(selectinload(Request.attempts))
    )
    return result.scalar_one().attempts


@pytest.fixture
async def fulfillment_db(tmp_path):
    init_engine(f"sqlite+aiosqlite:///{tmp_path}/manual.db")
    await create_all()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        user = User(
            email="manual-test@example.com", password_hash="x", role=UserRole.USER
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        yield session, user
    await dispose_engine()


def _settings(tmp_path, **overrides) -> Settings:
    return Settings(
        DATA_DIR=str(tmp_path / "data"),
        MUSIC_LIBRARY_PATH=str(tmp_path / "library"),
        SESSION_SECRET="x" * 40,
        **overrides,
    )


async def _make_request(session, user, **overrides) -> Request:
    fields = {
        "user_id": user.id,
        "title": "Labbar Bomma",
        "artist": "Sooraj Santhosh",
        "source": "apple",
        "source_id": "track-1",
        "dedupe_key": "apple:track-1",
        "status": RequestStatus.APPROVED,
    }
    fields.update(overrides)
    req = Request(**fields)
    session.add(req)
    await session.commit()
    await session.refresh(req)
    return req


class TestPinnedCandidateIsWhatGetsDownloaded:
    async def test_the_picked_video_is_the_one_requested_not_the_top_scorer(
        self, fulfillment_db, tmp_path, httpx_mock
    ):
        """The motivating case: scoring picked wrong, the requester picked right, honour it."""
        session, user = fulfillment_db
        request_row = await _make_request(
            session,
            user,
            manual_candidate={
                "backend": "ytdlp",
                "identifier": "rightpick02",
                "name": "Sooraj Santhosh, Ranina Reddy - Labbar Bomma",
                "quality": "lossy",
                "extra": {"uploader": "Aditya Music"},
            },
        )
        settings = _settings(tmp_path, YTDLP_SHIM_URL="http://localhost:8090")

        staging = settings.data_dir_path / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "labbar.opus").write_bytes(b"audio")

        httpx_mock.add_response(
            url="http://localhost:8090/downloads",
            method="POST",
            status_code=202,
            json={"job_id": "job-1"},
        )
        httpx_mock.add_response(
            url="http://localhost:8090/downloads/job-1",
            method="GET",
            json={"state": "completed", "filename": "labbar.opus", "error": None},
        )

        await fulfill_request(session, request_row, settings)

        assert request_row.status == RequestStatus.FULFILLED

        # The assertion that actually matters: the shim was asked for the requester's video,
        # and no search was ever performed, because a manual pick must not be re-scored.
        download_posts = [
            r
            for r in httpx_mock.get_requests()
            if r.url.path == "/downloads" and r.method == "POST"
        ]
        assert len(download_posts) == 1
        assert json.loads(download_posts[0].content)["video_id"] == "rightpick02"
        assert not [r for r in httpx_mock.get_requests() if r.url.path == "/search"]

    async def test_the_pin_is_consumed_so_a_later_retry_scores_normally(
        self, fulfillment_db, tmp_path, httpx_mock
    ):
        """A pinned pick is used once. Left set, an ordinary retry would silently re-run it."""
        session, user = fulfillment_db
        request_row = await _make_request(
            session,
            user,
            manual_candidate={
                "backend": "ytdlp",
                "identifier": "rightpick02",
                "name": "Sooraj Santhosh - Labbar Bomma",
                "quality": "lossy",
                "extra": {},
            },
        )
        settings = _settings(tmp_path, YTDLP_SHIM_URL="http://localhost:8090")

        # The pinned download fails outright, which is the interesting case: even then the pin
        # must not survive to be retried forever.
        httpx_mock.add_response(
            url="http://localhost:8090/downloads",
            method="POST",
            status_code=500,
        )

        await fulfill_request(session, request_row, settings)

        await session.refresh(request_row)
        assert request_row.manual_candidate is None
        assert request_row.status == RequestStatus.FAILED

    async def test_a_pinned_prowlarr_candidate_grabs_that_exact_release(
        self, fulfillment_db, tmp_path, httpx_mock
    ):
        """Per-tier dispatch: a Prowlarr pick must grab its guid via Prowlarr, not search again."""
        session, user = fulfillment_db
        request_row = await _make_request(
            session,
            user,
            manual_candidate={
                "backend": "prowlarr",
                "identifier": "the-chosen-guid",
                "name": "Sooraj Santhosh - Labbar Bomma [FLAC]",
                "quality": "lossless",
                "extra": {"indexerId": 7},
            },
        )
        settings = _settings(
            tmp_path,
            PROWLARR_URL="http://localhost:9696",
            PROWLARR_API_KEY="key",
            QBITTORRENT_URL="http://localhost:8080",
            QBITTORRENT_USERNAME="admin",
            QBITTORRENT_PASSWORD="pw",
        )

        # Reusable: after the grab the tier looks the torrent up again, which re-authenticates.
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/auth/login",
            method="POST",
            text="Ok.",
            is_reusable=True,
        )
        # Nothing already in qBittorrent, so the tier takes the grab path rather than adopting
        # an existing torrent. Returned as an empty list because find_torrent_by_hint parses
        # this as JSON.
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/torrents/info",
            method="GET",
            json=[],
            is_reusable=True,
        )
        # Prowlarr's grab API is a POST to /api/v1/search (see ProwlarrSearchSource.grab).
        httpx_mock.add_response(
            url="http://localhost:9696/api/v1/search",
            method="POST",
            status_code=200,
            json={},
        )

        await fulfill_request(session, request_row, settings)

        # The grab must have named the requester's own guid and the indexer it came from.
        # Everything after this (the torrent never appearing in qBittorrent) is mocked away;
        # what is being proved here is per-tier dispatch of the pinned pick.
        grabs = [
            r
            for r in httpx_mock.get_requests()
            if r.method == "POST" and r.url.path == "/api/v1/search"
        ]
        assert grabs, "expected a Prowlarr grab call for the pinned candidate"
        body = json.loads(grabs[0].content)
        assert body["guid"] == "the-chosen-guid"
        assert body["indexerId"] == 7

    async def test_no_search_is_performed_when_a_candidate_is_pinned(
        self, fulfillment_db, tmp_path, httpx_mock
    ):
        """Re-scoring would hand back the automatic choice the requester just overrode."""
        session, user = fulfillment_db
        request_row = await _make_request(
            session,
            user,
            manual_candidate={
                "backend": "ytdlp",
                "identifier": "pinned-vid",
                "name": "Pinned",
                "quality": "lossy",
                "extra": {},
            },
        )
        settings = _settings(
            tmp_path,
            YTDLP_SHIM_URL="http://localhost:8090",
            # Prowlarr configured too: without the manual path short-circuiting the ladder,
            # fulfillment would start at Prowlarr and search it.
            PROWLARR_URL="http://localhost:9696",
            PROWLARR_API_KEY="key",
        )

        httpx_mock.add_response(
            url="http://localhost:8090/downloads",
            method="POST",
            status_code=500,
        )

        await fulfill_request(session, request_row, settings)

        prowlarr_calls = [r for r in httpx_mock.get_requests() if "9696" in str(r.url)]
        assert prowlarr_calls == []


class TestPinnedCandidateStillHonoursTheLosslessContract:
    async def test_a_lossy_pin_against_a_live_lossless_ask_is_declined_not_downloaded(
        self, fulfillment_db, tmp_path, httpx_mock
    ):
        """Defence in depth: the route refuses this too, but no path may downgrade silently.

        A request can change underneath a pin (a rollback of an earlier "lossy is fine"), so
        the tier re-checks rather than trusting that the route already did.
        """
        session, user = fulfillment_db
        request_row = await _make_request(
            session,
            user,
            desired_quality="lossless",
            lossy_accepted=False,
            manual_candidate={
                "backend": "ytdlp",
                "identifier": "a-lossy-video",
                "name": "Labbar Bomma (Audio)",
                "quality": "lossy",
                "extra": {},
            },
        )
        settings = _settings(tmp_path, YTDLP_SHIM_URL="http://localhost:8090")

        await fulfill_request(session, request_row, settings)

        # Nothing was downloaded at all.
        assert not [r for r in httpx_mock.get_requests() if r.url.path == "/downloads"]
        # And it parks on the question rather than failing or silently settling.
        assert request_row.status == RequestStatus.AWAITING_QUALITY
        assert request_row.final_file_path is None

        attempts = await _attempts_of(session, request_row.id)
        assert attempts[-1].outcome == FulfillmentOutcome.DECLINED_LOSSY_ONLY.value
        assert "not lossless" in attempts[-1].detail

    async def test_a_lossy_pin_is_downloaded_once_the_requester_has_settled(
        self, fulfillment_db, tmp_path, httpx_mock
    ):
        session, user = fulfillment_db
        request_row = await _make_request(
            session,
            user,
            desired_quality="lossless",
            lossy_accepted=True,
            manual_candidate={
                "backend": "ytdlp",
                "identifier": "a-lossy-video",
                "name": "Labbar Bomma (Audio)",
                "quality": "lossy",
                "extra": {},
            },
        )
        settings = _settings(tmp_path, YTDLP_SHIM_URL="http://localhost:8090")

        staging = settings.data_dir_path / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "labbar.opus").write_bytes(b"audio")

        httpx_mock.add_response(
            url="http://localhost:8090/downloads",
            method="POST",
            status_code=202,
            json={"job_id": "job-9"},
        )
        httpx_mock.add_response(
            url="http://localhost:8090/downloads/job-9",
            method="GET",
            json={"state": "completed", "filename": "labbar.opus", "error": None},
        )

        await fulfill_request(session, request_row, settings)

        assert request_row.status == RequestStatus.FULFILLED
        # The shim is told "any", not the original "lossless", or it would refuse the job the
        # requester has already explicitly agreed to.
        posts = [
            r
            for r in httpx_mock.get_requests()
            if r.url.path == "/downloads" and r.method == "POST"
        ]
        assert json.loads(posts[0].content)["quality"] == "any"
