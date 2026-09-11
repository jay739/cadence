from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi import Request as FastAPIRequest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_admin
from app.core.limiter import (
    REQUEST_CREATE_RATE_LIMIT,
    REQUEST_RETRY_RATE_LIMIT,
    limiter,
)
from app.models.user import User, UserRole
from app.schemas.request import (
    ApproveRequestIn,
    CreateRequestIn,
    RejectRequestIn,
    RequestDetailOut,
    RequestOut,
)
from app.services.requests import (
    RequestNotFailedError,
    approve_request,
    create_or_get_existing_request,
    get_request_by_id,
    get_request_by_id_with_attempts,
    list_all_requests,
    list_pending_requests,
    list_requests_for_user,
    reject_request,
    retry_request,
)
from app.workers.fulfillment_worker import get_worker

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/requests", tags=["requests"])


def _ensure_owner_or_admin(request_row, user: User) -> None:
    if request_row.user_id != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Request not found"
        )


@router.post("", response_model=RequestOut, status_code=status.HTTP_201_CREATED)
@limiter.limit(REQUEST_CREATE_RATE_LIMIT)
async def create_request(
    request: FastAPIRequest,
    body: CreateRequestIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RequestOut:
    request_row, created = await create_or_get_existing_request(session, user, body)
    logger.info(
        "request_submitted",
        request_id=request_row.id,
        created=created,
        status=request_row.status.value,
    )
    return RequestOut.model_validate(request_row)


@router.get("/mine", response_model=list[RequestOut])
async def my_requests(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)
) -> list[RequestOut]:
    rows = await list_requests_for_user(session, user.id)
    return [RequestOut.model_validate(r) for r in rows]


@router.get("/{request_id}", response_model=RequestDetailOut)
async def get_request_detail(
    request_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RequestDetailOut:
    request_row = await get_request_by_id_with_attempts(session, request_id)
    if request_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Request not found"
        )
    _ensure_owner_or_admin(request_row, user)
    return RequestDetailOut.model_validate(request_row)


@router.get("/admin/pending", response_model=list[RequestOut])
async def admin_pending_requests(
    _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)
) -> list[RequestOut]:
    rows = await list_pending_requests(session)
    return [RequestOut.model_validate(r) for r in rows]


@router.get("/admin/all", response_model=list[RequestOut])
async def admin_all_requests(
    _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_db)
) -> list[RequestOut]:
    rows = await list_all_requests(session)
    return [RequestOut.model_validate(r) for r in rows]


@router.post("/{request_id}/approve", response_model=RequestOut)
async def admin_approve_request(
    request_id: str,
    body: ApproveRequestIn,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> RequestOut:
    request_row = await get_request_by_id(session, request_id)
    if request_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Request not found"
        )
    request_row = await approve_request(session, request_row, body)
    logger.info("request_approved", request_id=request_row.id, admin_id=admin.id)
    worker = get_worker()
    if worker is not None:
        worker.notify()
    return RequestOut.model_validate(request_row)


@router.post("/{request_id}/reject", response_model=RequestOut)
async def admin_reject_request(
    request_id: str,
    body: RejectRequestIn,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> RequestOut:
    request_row = await get_request_by_id(session, request_id)
    if request_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Request not found"
        )
    request_row = await reject_request(session, request_row, body)
    logger.info("request_rejected", request_id=request_row.id, admin_id=admin.id)
    return RequestOut.model_validate(request_row)


@router.post("/{request_id}/retry", response_model=RequestOut)
@limiter.limit(REQUEST_RETRY_RATE_LIMIT)
async def retry_failed_request(
    request: FastAPIRequest,
    request_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RequestOut:
    """Try a failed request again. The requester's own action, not an admin decision.

    Owner-or-admin, the same access `get_request_detail` already uses: unlike the initial
    approve/reject, which decides whether to spend fulfillment effort on a brand-new request,
    retry is "this one already failed, take another pass at it", so it does not need to route
    through an admin the way the first approval does.
    """
    request_row = await get_request_by_id(session, request_id)
    if request_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Request not found"
        )
    _ensure_owner_or_admin(request_row, user)
    try:
        request_row = await retry_request(session, request_row)
    except RequestNotFailedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only a failed request can be retried (this one is {exc.args[0]!r})",
        ) from exc
    logger.info("request_retried", request_id=request_row.id, user_id=user.id)
    worker = get_worker()
    if worker is not None:
        worker.notify()
    return RequestOut.model_validate(request_row)
