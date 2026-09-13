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
    PickCandidateIn,
    RejectRequestIn,
    RequestDetailOut,
    RequestOut,
)
from app.services.manual_candidate import (
    CandidateNotActionableError,
    CandidateNotFoundError,
    CandidateQualityRefusedError,
    RequestNotPickableError,
    resolve_manual_candidate,
)
from app.services.requests import (
    RequestNotAwaitingQualityError,
    RequestNotFailedError,
    accept_lossy_copy,
    approve_request,
    create_or_get_existing_request,
    delivered_by_backend,
    get_request_by_id,
    get_request_by_id_with_attempts,
    list_all_requests,
    list_pending_requests,
    list_requests_for_user,
    pick_candidate,
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


def _with_delivered_by(request_row) -> RequestOut:
    """Serialize a request, filling in the tier that actually delivered it.

    Set explicitly here rather than as a Pydantic computed field on the model: `delivered_by`
    is derived from the attempts relationship, and only the routes that eager-load it can
    answer the question at all. A computed field would serialize correctly on the detail route
    and raise MissingGreenlet on any route that did not think to eager-load, which is exactly
    the failure mode `get_request_by_id_with_attempts` documents.
    """
    out = RequestOut.model_validate(request_row)
    out.delivered_by = delivered_by_backend(request_row)
    return out


@router.get("/mine", response_model=list[RequestOut])
async def my_requests(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)
) -> list[RequestOut]:
    rows = await list_requests_for_user(session, user.id)
    return [_with_delivered_by(r) for r in rows]


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
    detail = RequestDetailOut.model_validate(request_row)
    detail.delivered_by = delivered_by_backend(request_row)
    return detail


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


@router.post("/{request_id}/pick-candidate", response_model=RequestOut)
@limiter.limit(REQUEST_RETRY_RATE_LIMIT)
async def pick_candidate_for_request(
    request: FastAPIRequest,
    request_id: str,
    body: PickCandidateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RequestOut:
    """Override a wrong automatic pick by naming a candidate from this request's audit trail.

    Owner-or-admin, like retry and accept-lossy: this is the requester correcting their own
    request, not an admin decision. It shares retry's rate limit for the same reason
    accept-lossy does, since it is the same shape of action, a user-initiated re-run of
    fulfillment for one request.

    The request body is a pointer (attempt id + index), never a candidate. The candidate
    itself is read back out of the FulfillmentAttempt rows Cadence wrote during a real
    fulfillment pass, so nothing a client sends can become a download target. Every refusal
    below names what was wrong and what to do instead, rather than a generic failure.
    """
    request_row = await get_request_by_id_with_attempts(session, request_id)
    if request_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Request not found"
        )
    _ensure_owner_or_admin(request_row, user)

    try:
        candidate = resolve_manual_candidate(
            request_row,
            list(request_row.attempts),
            attempt_id=body.attempt_id,
            candidate_index=body.candidate_index,
        )
    except RequestNotPickableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Only a request that is not already downloading or delivered can have its "
                f"download choice overridden (this one is {exc.args[0]!r})"
            ),
        ) from exc
    except CandidateQualityRefusedError as exc:
        # 409, not 422: the pointer resolved perfectly well, and the conflict is with the
        # request's own still-standing lossless ask.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except (CandidateNotFoundError, CandidateNotActionableError) as exc:
        # 422: the body was well-formed but does not point at something downloadable.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    request_row = await pick_candidate(session, request_row, candidate)
    logger.info(
        "request_candidate_picked",
        request_id=request_row.id,
        user_id=user.id,
        backend=candidate["backend"],
    )
    worker = get_worker()
    if worker is not None:
        worker.notify()
    return RequestOut.model_validate(request_row)


@router.post("/{request_id}/accept-lossy", response_model=RequestOut)
@limiter.limit(REQUEST_RETRY_RATE_LIMIT)
async def accept_lossy_copy_for_request(
    request: FastAPIRequest,
    request_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RequestOut:
    """Answer "nothing lossless was found, accept a lossy copy?" with yes, and resume.

    Owner-or-admin, like retry and the detail view: this is the requester answering a question
    about their own request, not an admin decision. It shares retry's rate limit because it is
    the same shape of action -- a user-initiated re-run of fulfillment for one request.
    """
    request_row = await get_request_by_id(session, request_id)
    if request_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Request not found"
        )
    _ensure_owner_or_admin(request_row, user)
    try:
        request_row = await accept_lossy_copy(session, request_row)
    except RequestNotAwaitingQualityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Only a request waiting on a quality decision can accept a lossy copy "
                f"(this one is {exc.args[0]!r})"
            ),
        ) from exc
    logger.info("request_lossy_accepted", request_id=request_row.id, user_id=user.id)
    worker = get_worker()
    if worker is not None:
        worker.notify()
    return RequestOut.model_validate(request_row)
