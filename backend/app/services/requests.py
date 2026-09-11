"""Request creation, idempotency, and admin approval/rejection logic."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.request import Request, RequestStatus
from app.models.user import User
from app.schemas.request import ApproveRequestIn, CreateRequestIn, RejectRequestIn
from app.services.webhook_delivery import notify_webhook


def compute_dedupe_key(source: str, source_id: str) -> str:
    return f"{source}:{source_id}"


async def get_request_by_dedupe_key(
    session: AsyncSession, user_id: str, dedupe_key: str
) -> Request | None:
    result = await session.execute(
        select(Request).where(
            Request.user_id == user_id, Request.dedupe_key == dedupe_key
        )
    )
    return result.scalar_one_or_none()


async def get_request_by_id(session: AsyncSession, request_id: str) -> Request | None:
    result = await session.execute(select(Request).where(Request.id == request_id))
    return result.scalar_one_or_none()


async def get_request_by_id_with_attempts(
    session: AsyncSession, request_id: str
) -> Request | None:
    """Eager-loads the fulfillment attempts audit trail alongside the request.

    Used by the request-detail route: Pydantic's from_attributes serialization happens after
    the route handler returns, outside the session's greenlet context, so any lazy-loaded
    relationship accessed at that point raises MissingGreenlet. Eager loading here avoids that.
    """
    result = await session.execute(
        select(Request)
        .where(Request.id == request_id)
        .options(selectinload(Request.attempts))
    )
    return result.scalar_one_or_none()


class RequestTerminallyRejectedResubmit(Exception):
    """Raised when a caller needs to know a prior rejection was cleared for re-request."""


async def create_or_get_existing_request(
    session: AsyncSession, user: User, body: CreateRequestIn
) -> tuple[Request, bool]:
    """Create a new request, or return the existing one for this user+track (idempotent).

    Returns (request, created). A second request for a track already pending/approved/
    downloading/fulfilled surfaces the existing row rather than creating a duplicate. A
    previously *rejected* request may be re-requested (the mission: "rejection is terminal
    unless the user re-requests") -- in that case we reset it back to pending rather than
    creating a second row, preserving its history/audit trail.
    A previously *failed* request may also be retried the same way.
    """
    dedupe_key = compute_dedupe_key(body.source, body.source_id)
    existing = await get_request_by_dedupe_key(session, user.id, dedupe_key)

    if existing is not None:
        if existing.status in (RequestStatus.REJECTED, RequestStatus.FAILED):
            existing.status = RequestStatus.PENDING
            existing.admin_note = None
            existing.failure_reason = None
            existing.worker_lock_token = None
            await session.commit()
            await session.refresh(existing)
            return existing, False
        return existing, False

    new_request = Request(
        user_id=user.id,
        title=body.title,
        artist=body.artist,
        album=body.album,
        duration_seconds=body.duration_seconds,
        artwork_url=body.artwork_url,
        source=body.source,
        source_id=body.source_id,
        musicbrainz_recording_id=body.musicbrainz_recording_id,
        preferred_format=body.preferred_format,
        dedupe_key=dedupe_key,
        status=RequestStatus.PENDING,
    )
    session.add(new_request)
    await session.commit()
    await session.refresh(new_request)
    return new_request, True


async def list_requests_for_user(session: AsyncSession, user_id: str) -> list[Request]:
    result = await session.execute(
        select(Request)
        .where(Request.user_id == user_id)
        .order_by(Request.created_at.desc())
    )
    return list(result.scalars().all())


async def list_pending_requests(session: AsyncSession) -> list[Request]:
    result = await session.execute(
        select(Request)
        .where(Request.status == RequestStatus.PENDING)
        .order_by(Request.created_at.asc())
    )
    return list(result.scalars().all())


async def list_all_requests(session: AsyncSession) -> list[Request]:
    result = await session.execute(select(Request).order_by(Request.created_at.desc()))
    return list(result.scalars().all())


async def approve_request(
    session: AsyncSession, request_row: Request, body: ApproveRequestIn
) -> Request:
    request_row.status = RequestStatus.APPROVED
    request_row.admin_note = body.note
    await session.commit()
    await session.refresh(request_row)

    # Strictly after the commit above, never before or interleaved with it: a webhook
    # delivery failure must never affect the approval that already succeeded.
    owner = await session.get(User, request_row.user_id)
    if owner is not None:
        await notify_webhook(owner, request_row, "approved")

    return request_row


async def reject_request(
    session: AsyncSession, request_row: Request, body: RejectRequestIn
) -> Request:
    request_row.status = RequestStatus.REJECTED
    request_row.admin_note = body.note
    await session.commit()
    await session.refresh(request_row)
    return request_row


class RequestNotFailedError(Exception):
    """Retry only makes sense on a request that actually failed.

    Unlike approve_request (which has always applied unconditionally to any status, mechanically
    correct but not obviously a "retry" action), retry is guarded: it should read as "try this
    specific failure again", not as a second way to approve a pending request.
    """


async def retry_request(session: AsyncSession, request_row: Request) -> Request:
    if request_row.status != RequestStatus.FAILED:
        raise RequestNotFailedError(request_row.status.value)
    request_row.status = RequestStatus.APPROVED
    # Cleared, not preserved: a stale reason from the LAST attempt would be actively
    # misleading once a fresh attempt is in flight, and the full history survives regardless
    # in the FulfillmentAttempt audit trail (fulfillment_attempts_count is never reset either,
    # for the same reason: it is a lifetime count across every attempt this request has ever
    # made, not just the most recent one).
    request_row.failure_reason = None
    await session.commit()
    await session.refresh(request_row)
    return request_row
