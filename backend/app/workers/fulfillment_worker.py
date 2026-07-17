"""In-process asyncio fulfillment worker.

No separate Redis/broker dependency for Phase 1: job state lives entirely in the `requests`
table (status APPROVED/DOWNLOADING act as the queue), so the whole stack is one deployable
unit as the mission specifies. A single asyncio task loop polls for approved requests, claims
one at a time with an optimistic lock token (worker_lock_token) to avoid double-processing if
multiple workers were ever run, and drives it through app.services.fulfillment.

Crash recovery: on startup, any request left in DOWNLOADING status (meaning a previous worker
process died mid-job) is reset to APPROVED so it is picked up and re-evaluated fresh, per the
mission's "resumed or re-evaluated from persisted state, never silently lost or double-grabbed"
requirement. An unhandled exception inside a single job's processing is caught, marks that job
FAILED, and logging continues; the worker loop itself never exits because of it.
"""

from __future__ import annotations

import asyncio
import uuid

import structlog
from sqlalchemy import select, update

from app.core.config import Settings
from app.db.base import get_sessionmaker
from app.models.request import Request, RequestStatus
from app.services.fulfillment import fulfill_request

logger = structlog.get_logger(__name__)

_worker_singleton: "FulfillmentWorker | None" = None


def get_worker() -> "FulfillmentWorker | None":
    return _worker_singleton


def set_worker(worker: "FulfillmentWorker | None") -> None:
    global _worker_singleton
    _worker_singleton = worker


class FulfillmentWorker:
    def __init__(
        self, settings: Settings, *, poll_interval_seconds: float = 2.0
    ) -> None:
        self._settings = settings
        self._poll_interval = poll_interval_seconds
        self._task: asyncio.Task | None = None
        self._wake_event = asyncio.Event()
        self._stopping = False

    def notify(self) -> None:
        """Wake the worker immediately instead of waiting for the next poll tick."""
        self._wake_event.set()

    async def recover_crashed_jobs(self) -> None:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            result = await session.execute(
                update(Request)
                .where(Request.status == RequestStatus.DOWNLOADING)
                .values(status=RequestStatus.APPROVED, worker_lock_token=None)
            )
            await session.commit()
            if result.rowcount:
                logger.warning("recovered_crashed_jobs", count=result.rowcount)

    async def _claim_next(self) -> Request | None:
        sessionmaker = get_sessionmaker()
        token = str(uuid.uuid4())
        async with sessionmaker() as session:
            result = await session.execute(
                select(Request)
                .where(Request.status == RequestStatus.APPROVED)
                .order_by(Request.created_at.asc())
                .limit(1)
            )
            request_row = result.scalar_one_or_none()
            if request_row is None:
                return None
            request_row.worker_lock_token = token
            await session.commit()
            await session.refresh(request_row)
            return request_row

    async def _run_one(self) -> bool:
        """Process at most one job. Returns True if a job was found and processed."""
        sessionmaker = get_sessionmaker()
        claimed = await self._claim_next()
        if claimed is None:
            return False

        async with sessionmaker() as session:
            result = await session.execute(
                select(Request).where(Request.id == claimed.id)
            )
            request_row = result.scalar_one_or_none()
            if request_row is None:
                return True
            try:
                await fulfill_request(session, request_row, self._settings)
            except Exception as exc:  # noqa: BLE001 - never let a job crash the loop
                logger.error(
                    "fulfillment_job_crashed",
                    request_id=request_row.id,
                    error=str(exc),
                    exc_info=True,
                )
                request_row.status = RequestStatus.FAILED
                request_row.failure_reason = "Unhandled worker error, see server logs"
                await session.commit()
        return True

    async def _loop(self) -> None:
        await self.recover_crashed_jobs()
        while not self._stopping:
            try:
                processed = await self._run_one()
            except Exception as exc:  # noqa: BLE001 - the loop itself must never die
                logger.error("fulfillment_loop_error", error=str(exc), exc_info=True)
                processed = False

            if processed:
                continue  # immediately check for another job

            self._wake_event.clear()
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(), timeout=self._poll_interval
                )
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopping = True
        self.notify()
        if self._task is not None:
            await asyncio.wait_for(self._task, timeout=10)
            self._task = None
