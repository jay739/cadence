"""Profile routes for the signed-in user: request stats, avatar/email edits, password change.

Everything here operates on the caller's own account (`/me/...`): there is no admin-on-behalf-
of-another-user variant, since nothing in the mission calls for one.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.routes_auth import build_user_out, issue_token_response
from app.core.config import Settings, get_settings
from app.models.user import User
from app.schemas.user import (
    ChangePasswordIn,
    RequestStatusCounts,
    TokenOut,
    UpdateAvatarIn,
    UpdateEmailIn,
    UserOut,
)
from app.services.requests import get_request_status_counts
from app.services.users import (
    EmailAlreadyRegisteredError,
    PasswordChangeNotSupportedError,
    WrongPasswordError,
    change_password,
    update_avatar,
    update_email,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/me/stats", response_model=RequestStatusCounts)
async def my_stats(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RequestStatusCounts:
    counts = await get_request_status_counts(session, user.id)
    return RequestStatusCounts(**counts, total=sum(counts.values()))


@router.patch("/me/avatar", response_model=UserOut)
async def patch_avatar(
    body: UpdateAvatarIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserOut:
    updated = await update_avatar(session, user, body.avatar_url)
    return build_user_out(updated, settings)


@router.patch("/me/email", response_model=UserOut)
async def patch_email(
    body: UpdateEmailIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserOut:
    try:
        updated = await update_email(session, user, body.email)
    except EmailAlreadyRegisteredError as exc:
        logger.info("profile_email_conflict")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc
    logger.info("profile_email_updated", user_id=user.id)
    return build_user_out(updated, settings)


@router.post("/me/password", response_model=TokenOut)
async def post_change_password(
    body: ChangePasswordIn,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenOut:
    """Change a LOCAL account's password. Navidrome-backed accounts have nothing to change
    here at all -- this endpoint is inapplicable for them, and the frontend must never render
    a password form for one in the first place (it shows the Navidrome redirect link instead).
    """
    try:
        updated = await change_password(
            session,
            user,
            current_password=body.current_password,
            new_password=body.new_password,
        )
    except PasswordChangeNotSupportedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This account has no local password to change. "
            "Manage your password through Navidrome instead.",
        ) from exc
    except WrongPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        ) from exc
    logger.info("profile_password_changed", user_id=user.id)
    # A fresh token, since the token_version bump above invalidates the one this very request
    # was authenticated with -- without this the user would be logged out by their own change.
    return issue_token_response(response, updated, settings)
