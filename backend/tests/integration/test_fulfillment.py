"""Integration tests for the Prowlarr-first, Soulseek-fallback fulfillment decision logic.

Prowlarr/qBittorrent/slskd are all mocked at the HTTP layer (via pytest-httpx), so the
fallback logic is provably correct without any live services, per the mission's requirement.
"""

from __future__ import annotations

import re

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
    """Fetch a Request's fulfillment_attempts audit trail via an explicit eager-loaded query.

    Async SQLAlchemy relationship collections cannot be lazy-loaded via plain attribute access
    outside an explicit await (that raises MissingGreenlet), so tests inspect the audit trail
    through this helper rather than touching `request_row.attempts` directly.
    """
    result = await session.execute(
        select(Request)
        .where(Request.id == request_id)
        .options(selectinload(Request.attempts))
    )
    return result.scalar_one().attempts


@pytest.fixture
async def fulfillment_db(tmp_path):
    init_engine(f"sqlite+aiosqlite:///{tmp_path}/fulfillment.db")
    await create_all()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        user = User(
            email="fulfillment-test@example.com", password_hash="x", role=UserRole.USER
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


async def _make_request(session, user) -> Request:
    req = Request(
        user_id=user.id,
        title="Tum Hi Ho",
        artist="Arijit Singh",
        album="Aashiqui 2",
        source="apple",
        source_id="track-1",
        dedupe_key="apple:track-1",
        status=RequestStatus.APPROVED,
    )
    session.add(req)
    await session.commit()
    await session.refresh(req)
    return req


class TestNeitherBackendConfigured:
    async def test_fails_cleanly_with_no_backends_configured(
        self, fulfillment_db, tmp_path
    ):
        session, user = fulfillment_db
        request_row = await _make_request(session, user)
        settings = _settings(tmp_path)

        await fulfill_request(session, request_row, settings)

        assert request_row.status == RequestStatus.FAILED
        assert request_row.failure_reason
        attempts = await _attempts_of(session, request_row.id)
        assert any(
            a.outcome == FulfillmentOutcome.SKIPPED_NOT_CONFIGURED.value
            for a in attempts
        )

    async def test_audit_trail_records_both_backends_skipped(
        self, fulfillment_db, tmp_path
    ):
        session, user = fulfillment_db
        request_row = await _make_request(session, user)
        settings = _settings(tmp_path)

        await fulfill_request(session, request_row, settings)

        attempts = await _attempts_of(session, request_row.id)
        backends_tried = {a.backend for a in attempts}
        assert backends_tried == {"prowlarr", "slskd"}


class TestProwlarrOnly:
    async def test_prowlarr_no_candidates_falls_through_to_failed(
        self, fulfillment_db, tmp_path, httpx_mock
    ):
        session, user = fulfillment_db
        request_row = await _make_request(session, user)
        settings = _settings(
            tmp_path, PROWLARR_URL="http://localhost:9696", PROWLARR_API_KEY="key123"
        )

        httpx_mock.add_response(
            url=re.compile(r"http://localhost:9696/api/v1/search.*"), json=[]
        )

        await fulfill_request(session, request_row, settings)

        assert request_row.status == RequestStatus.FAILED
        attempts = await _attempts_of(session, request_row.id)
        prowlarr_attempts = [a for a in attempts if a.backend == "prowlarr"]
        assert (
            prowlarr_attempts[0].outcome
            == FulfillmentOutcome.SEARCHED_NO_CANDIDATES.value
        )

    async def test_prowlarr_no_qualifying_candidate_recorded(
        self, fulfillment_db, tmp_path, httpx_mock
    ):
        session, user = fulfillment_db
        request_row = await _make_request(session, user)
        settings = _settings(
            tmp_path, PROWLARR_URL="http://localhost:9696", PROWLARR_API_KEY="key123"
        )

        httpx_mock.add_response(
            url=re.compile(r"http://localhost:9696/api/v1/search.*"),
            json=[
                {
                    "guid": "guid-1",
                    "title": "Completely Unrelated Song Name Here",
                    "indexerId": 1,
                    "indexer": "TestIndexer",
                    "size": 1000,
                }
            ],
        )

        await fulfill_request(session, request_row, settings)

        attempts = await _attempts_of(session, request_row.id)
        prowlarr_attempts = [a for a in attempts if a.backend == "prowlarr"]
        assert (
            prowlarr_attempts[0].outcome
            == FulfillmentOutcome.SEARCHED_NO_QUALIFYING_CANDIDATE.value
        )
        assert request_row.status == RequestStatus.FAILED

    async def test_prowlarr_grab_failure_recorded_as_error(
        self, fulfillment_db, tmp_path, httpx_mock
    ):
        session, user = fulfillment_db
        request_row = await _make_request(session, user)
        settings = _settings(
            tmp_path, PROWLARR_URL="http://localhost:9696", PROWLARR_API_KEY="key123"
        )

        httpx_mock.add_response(
            url=re.compile(r"http://localhost:9696/api/v1/search\?.*"),
            method="GET",
            json=[
                {
                    "guid": "guid-1",
                    "title": "Arijit Singh - Tum Hi Ho",
                    "indexerId": 1,
                    "indexer": "TestIndexer",
                    "size": 1000,
                }
            ],
        )
        httpx_mock.add_response(
            url="http://localhost:9696/api/v1/search", method="POST", status_code=500
        )

        await fulfill_request(session, request_row, settings)

        attempts = await _attempts_of(session, request_row.id)
        prowlarr_attempts = [a for a in attempts if a.backend == "prowlarr"]
        assert any(
            a.outcome == FulfillmentOutcome.ERROR.value for a in prowlarr_attempts
        )


class TestProwlarrSucceedsViaQbittorrent:
    async def test_prowlarr_grab_and_qbittorrent_completion_marks_fulfilled(
        self, fulfillment_db, tmp_path, httpx_mock
    ):
        session, user = fulfillment_db
        request_row = await _make_request(session, user)
        library_dir = tmp_path / "library"
        data_dir = tmp_path / "data"
        settings = _settings(
            tmp_path,
            PROWLARR_URL="http://localhost:9696",
            PROWLARR_API_KEY="key123",
            QBITTORRENT_URL="http://localhost:8080",
            QBITTORRENT_USERNAME="admin",
            QBITTORRENT_PASSWORD="adminpass",
        )

        release_name = "Arijit Singh - Tum Hi Ho [FLAC]"
        httpx_mock.add_response(
            url=re.compile(r"http://localhost:9696/api/v1/search\?.*"),
            method="GET",
            json=[
                {
                    "guid": "guid-1",
                    "title": release_name,
                    "indexerId": 1,
                    "indexer": "TestIndexer",
                    "size": 30_000_000,
                }
            ],
        )
        httpx_mock.add_response(
            url="http://localhost:9696/api/v1/search", method="POST", json={}
        )

        # qBittorrent: login, find torrent by hint, confirm complete, list files.
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/auth/login",
            text="Ok.",
            headers={"Set-Cookie": "SID=abc123; Path=/"},
        )
        content_dir = data_dir / "staging"
        content_dir.mkdir(parents=True, exist_ok=True)
        (content_dir / "Tum Hi Ho.flac").write_bytes(b"fLaC" + b"\x00" * 40)
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/torrents/info",
            json=[
                {
                    "name": release_name,
                    "hash": "torrenthash1",
                    "save_path": str(content_dir),
                }
            ],
        )
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/torrents/info?hashes=torrenthash1",
            json=[{"progress": 1.0}],
        )
        httpx_mock.add_response(
            url="http://localhost:8080/api/v2/torrents/files?hash=torrenthash1",
            json=[{"name": "Tum Hi Ho.flac", "size": 30_000_000}],
        )

        await fulfill_request(session, request_row, settings)

        assert request_row.status == RequestStatus.FULFILLED
        assert request_row.final_file_path
        final_path = library_dir / request_row.final_file_path
        assert final_path.exists()

        attempts = await _attempts_of(session, request_row.id)
        outcomes = {a.outcome for a in attempts if a.backend == "prowlarr"}
        assert FulfillmentOutcome.GRABBED.value in outcomes
        assert FulfillmentOutcome.DOWNLOAD_COMPLETED.value in outcomes


class TestProwlarrFallsBackToSlskd:
    async def test_prowlarr_empty_then_slskd_succeeds_marks_fulfilled(
        self, fulfillment_db, tmp_path, httpx_mock
    ):
        session, user = fulfillment_db
        request_row = await _make_request(session, user)
        library_dir = tmp_path / "library"
        data_dir = tmp_path / "data"
        settings = _settings(
            tmp_path,
            PROWLARR_URL="http://localhost:9696",
            PROWLARR_API_KEY="key123",
            SLSKD_URL="http://localhost:5030",
            SLSKD_API_KEY="slskdkey",
        )

        # Prowlarr returns nothing.
        httpx_mock.add_response(
            url=re.compile(r"http://localhost:9696/api/v1/search.*"), json=[]
        )

        # slskd search lifecycle: create search, poll until complete, fetch responses.
        httpx_mock.add_response(
            url="http://localhost:5030/api/v0/searches",
            method="POST",
            json={"id": "search-1"},
        )
        httpx_mock.add_response(
            url="http://localhost:5030/api/v0/searches/search-1",
            method="GET",
            json={"isComplete": True},
        )
        httpx_mock.add_response(
            url="http://localhost:5030/api/v0/searches/search-1/responses",
            method="GET",
            json=[
                {
                    "username": "someuser",
                    "files": [
                        {
                            "filename": "Arijit Singh - Tum Hi Ho.flac",
                            "size": 30_000_000,
                            "bitRate": 1411,
                        }
                    ],
                }
            ],
        )
        httpx_mock.add_response(
            url="http://localhost:5030/api/v0/transfers/downloads/someuser",
            method="POST",
            json={},
        )
        httpx_mock.add_response(
            url="http://localhost:5030/api/v0/transfers/downloads/someuser",
            method="GET",
            json={
                "directories": [
                    {
                        "files": [
                            {
                                "filename": "Arijit Singh - Tum Hi Ho.flac",
                                "state": "Completed, Succeeded",
                            }
                        ]
                    }
                ]
            },
        )

        # Stage the "downloaded" file where fulfillment expects to find it (staging_dir).
        staging_dir = data_dir / "staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        (staging_dir / "Arijit Singh - Tum Hi Ho.flac").write_bytes(
            b"fLaC" + b"\x00" * 40
        )

        await fulfill_request(session, request_row, settings)

        assert request_row.status == RequestStatus.FULFILLED
        assert request_row.final_file_path
        final_path = library_dir / request_row.final_file_path
        assert final_path.exists()

        attempts = await _attempts_of(session, request_row.id)
        backends_tried = [a.backend for a in attempts]
        assert "prowlarr" in backends_tried
        assert "slskd" in backends_tried

    async def test_both_backends_exhausted_marks_failed_with_reason(
        self, fulfillment_db, tmp_path, httpx_mock
    ):
        session, user = fulfillment_db
        request_row = await _make_request(session, user)
        settings = _settings(
            tmp_path,
            PROWLARR_URL="http://localhost:9696",
            PROWLARR_API_KEY="key123",
            SLSKD_URL="http://localhost:5030",
            SLSKD_API_KEY="slskdkey",
        )

        httpx_mock.add_response(
            url=re.compile(r"http://localhost:9696/api/v1/search.*"), json=[]
        )
        httpx_mock.add_response(
            url="http://localhost:5030/api/v0/searches",
            method="POST",
            json={"id": "search-1"},
        )
        httpx_mock.add_response(
            url="http://localhost:5030/api/v0/searches/search-1",
            method="GET",
            json={"isComplete": True},
        )
        httpx_mock.add_response(
            url="http://localhost:5030/api/v0/searches/search-1/responses",
            method="GET",
            json=[],
        )

        await fulfill_request(session, request_row, settings)

        assert request_row.status == RequestStatus.FAILED
        assert (
            "Soulseek" in request_row.failure_reason
            or "indexer" in request_row.failure_reason
        )


class TestCrashRecoveryReEvaluation:
    async def test_already_fulfilled_request_is_not_reprocessed(
        self, fulfillment_db, tmp_path
    ):
        session, user = fulfillment_db
        request_row = await _make_request(session, user)
        request_row.status = RequestStatus.FULFILLED
        request_row.final_file_path = "Artist/Title.flac"
        await session.commit()

        settings = _settings(tmp_path)
        await fulfill_request(session, request_row, settings)

        # Must remain untouched; fulfill_request short-circuits for already-fulfilled requests.
        assert request_row.status == RequestStatus.FULFILLED
        assert request_row.final_file_path == "Artist/Title.flac"


class TestFulfillmentUsesDbSavedSettings:
    """Proves the worker's read path, not just /api/health and /api/search, honors a Prowlarr
    connection saved through the admin settings UI (no env var, no restart, no re-creating the
    Settings object passed to fulfill_request -- exactly what a running FulfillmentWorker would
    have in hand when an admin saves settings mid-session)."""

    async def test_prowlarr_saved_only_in_db_is_used_by_fulfillment(
        self, fulfillment_db, tmp_path, monkeypatch, httpx_mock
    ):
        monkeypatch.delenv("PROWLARR_URL", raising=False)
        monkeypatch.delenv("PROWLARR_API_KEY", raising=False)
        session, user = fulfillment_db
        request_row = await _make_request(session, user)

        # settings has NO Prowlarr fields set at construction time or via env; only a DB row,
        # saved the same way the admin settings PUT route saves one.
        settings = _settings(tmp_path)
        key = settings.resolved_settings_encryption_key()
        from app.services.settings_store import upsert_setting

        await upsert_setting(
            session,
            field_name="prowlarr_url",
            value="http://localhost:9697",
            key=key,
            is_secret=False,
        )
        await upsert_setting(
            session,
            field_name="prowlarr_api_key",
            value="db-saved-key",
            key=key,
            is_secret=True,
        )

        httpx_mock.add_response(
            url=re.compile(r"http://localhost:9697/api/v1/search.*"), json=[]
        )

        await fulfill_request(session, request_row, settings)

        attempts = await _attempts_of(session, request_row.id)
        prowlarr_attempts = [a for a in attempts if a.backend == "prowlarr"]
        # SEARCHED_NO_CANDIDATES (not SKIPPED_NOT_CONFIGURED) proves Prowlarr was actually
        # called using the DB-saved URL/key, not skipped as unconfigured.
        assert (
            prowlarr_attempts[0].outcome
            == FulfillmentOutcome.SEARCHED_NO_CANDIDATES.value
        )
