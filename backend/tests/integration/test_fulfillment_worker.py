"""Tests for the in-process fulfillment worker: claiming, crash recovery, loop resilience."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.base import create_all, get_sessionmaker, init_engine
from app.models.request import Request, RequestStatus
from app.models.user import User, UserRole
from app.workers.fulfillment_worker import FulfillmentWorker

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def worker_db(tmp_path):
    init_engine(f"sqlite+aiosqlite:///{tmp_path}/worker.db")
    await create_all()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        user = User(
            email="worker-test@example.com", password_hash="x", role=UserRole.USER
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        yield session, user


def _settings(tmp_path) -> Settings:
    return Settings(
        DATA_DIR=str(tmp_path / "data"),
        MUSIC_LIBRARY_PATH=str(tmp_path / "library"),
        SESSION_SECRET="x" * 40,
    )


class TestCrashRecovery:
    async def test_downloading_requests_reset_to_approved_on_recovery(
        self, worker_db, tmp_path
    ):
        session, user = worker_db
        stuck = Request(
            user_id=user.id,
            title="Stuck Song",
            artist="Some Artist",
            source="apple",
            source_id="stuck-1",
            dedupe_key="apple:stuck-1",
            status=RequestStatus.DOWNLOADING,
            worker_lock_token="old-token-from-dead-process",
        )
        session.add(stuck)
        await session.commit()

        worker = FulfillmentWorker(_settings(tmp_path))
        await worker.recover_crashed_jobs()

        # recover_crashed_jobs() updates via its own separate session, so this test's
        # session must repopulate its identity-mapped instance from a fresh row rather than
        # trust its own in-memory copy (populate_existing avoids a lazy-refresh round trip,
        # which would need its own await and isn't safe to trigger via plain expire_all()).
        result = await session.execute(
            select(Request)
            .where(Request.id == stuck.id)
            .execution_options(populate_existing=True)
        )
        recovered = result.scalar_one()
        assert recovered.status == RequestStatus.APPROVED
        assert recovered.worker_lock_token is None

    async def test_pending_and_fulfilled_requests_untouched_by_recovery(
        self, worker_db, tmp_path
    ):
        session, user = worker_db
        pending = Request(
            user_id=user.id,
            title="Pending Song",
            artist="Artist",
            source="apple",
            source_id="pending-1",
            dedupe_key="apple:pending-1",
            status=RequestStatus.PENDING,
        )
        fulfilled = Request(
            user_id=user.id,
            title="Fulfilled Song",
            artist="Artist",
            source="apple",
            source_id="fulfilled-1",
            dedupe_key="apple:fulfilled-1",
            status=RequestStatus.FULFILLED,
            final_file_path="Artist/Fulfilled Song.flac",
        )
        session.add_all([pending, fulfilled])
        await session.commit()

        worker = FulfillmentWorker(_settings(tmp_path))
        await worker.recover_crashed_jobs()

        result = await session.execute(
            select(Request)
            .where(Request.id == pending.id)
            .execution_options(populate_existing=True)
        )
        assert result.scalar_one().status == RequestStatus.PENDING
        result = await session.execute(
            select(Request)
            .where(Request.id == fulfilled.id)
            .execution_options(populate_existing=True)
        )
        assert result.scalar_one().status == RequestStatus.FULFILLED


class TestClaimNext:
    async def test_claim_next_picks_oldest_approved_request(self, worker_db, tmp_path):
        session, user = worker_db
        older = Request(
            user_id=user.id,
            title="Older",
            artist="Artist",
            source="apple",
            source_id="older-1",
            dedupe_key="apple:older-1",
            status=RequestStatus.APPROVED,
        )
        session.add(older)
        await session.commit()

        newer = Request(
            user_id=user.id,
            title="Newer",
            artist="Artist",
            source="apple",
            source_id="newer-1",
            dedupe_key="apple:newer-1",
            status=RequestStatus.APPROVED,
        )
        session.add(newer)
        await session.commit()

        worker = FulfillmentWorker(_settings(tmp_path))
        claimed = await worker._claim_next()
        assert claimed is not None
        assert claimed.id == older.id
        assert claimed.worker_lock_token is not None

    async def test_claim_next_returns_none_when_nothing_approved(
        self, worker_db, tmp_path
    ):
        worker = FulfillmentWorker(_settings(tmp_path))
        claimed = await worker._claim_next()
        assert claimed is None


class TestRunOneJobCrashIsolation:
    async def test_job_crash_marks_failed_without_raising(
        self, worker_db, tmp_path, monkeypatch
    ):
        session, user = worker_db
        req = Request(
            user_id=user.id,
            title="Will Crash",
            artist="Artist",
            source="apple",
            source_id="crash-1",
            dedupe_key="apple:crash-1",
            status=RequestStatus.APPROVED,
        )
        session.add(req)
        await session.commit()

        async def _boom(*args, **kwargs):
            raise RuntimeError("simulated crash during fulfillment")

        monkeypatch.setattr("app.workers.fulfillment_worker.fulfill_request", _boom)

        worker = FulfillmentWorker(_settings(tmp_path))
        processed = await worker._run_one()
        assert processed is True

        # _run_one() does its work through its own separate sessions (via get_sessionmaker()),
        # so this test must repopulate its identity-mapped instance from a fresh row.
        result = await session.execute(
            select(Request)
            .where(Request.id == req.id)
            .execution_options(populate_existing=True)
        )
        after = result.scalar_one()
        assert after.status == RequestStatus.FAILED
        assert after.failure_reason
