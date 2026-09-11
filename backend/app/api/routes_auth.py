from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.config import Settings, get_settings
from app.core.limiter import AUTH_RATE_LIMIT, limiter
from app.core.security import create_session_token
from app.models.user import User, UserRole
from app.schemas.user import LoginIn, RegisterIn, TokenOut, UserOut, WebhookUrlIn
from app.services.users import (
    EmailAlreadyRegisteredError,
    authenticate,
    create_user,
    set_webhook_url,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "cadence_session"


def _issue_token_response(
    response: Response, user: User, settings: Settings
) -> TokenOut:
    token = create_session_token(
        user_id=user.id,
        role=user.role.value,
        token_version=user.token_version,
        secret=settings.resolved_session_secret(),
        ttl_minutes=settings.session_ttl_minutes,
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_minutes * 60,
        path="/",
    )
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
@limiter.limit(AUTH_RATE_LIMIT)
async def register(
    request: Request,
    body: RegisterIn,
    response: Response,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenOut:
    try:
        user = await create_user(
            session, email=body.email, password=body.password, role=UserRole.USER
        )
    except EmailAlreadyRegisteredError as exc:
        logger.info("register_email_conflict")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc
    logger.info("user_registered", user_id=user.id)
    return _issue_token_response(response, user, settings)


@router.post("/login", response_model=TokenOut)
@limiter.limit(AUTH_RATE_LIMIT)
async def login(
    request: Request,
    body: LoginIn,
    response: Response,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenOut:
    user = await authenticate(
        session,
        identifier=body.identifier,
        password=body.password,
        navidrome_url=settings.navidrome_url,
    )
    if user is None:
        logger.info("login_failed")
        # Never say "email": identifier is a Navidrome username as often as it is a local
        # account's email, and a wrong-provider error message defeats the point of one form
        # for both.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username/email or password",
        )
    logger.info("login_succeeded", user_id=user.id)
    return _issue_token_response(response, user, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("/me/webhook", response_model=UserOut)
async def update_webhook_url(
    body: WebhookUrlIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> UserOut:
    """Set or clear the caller's own webhook URL.

    Cadence will POST a small JSON notification here whenever one of this user's own requests
    is approved or fulfilled (never for rejected/failed). Generic webhook URL only for now --
    per-service integrations (Telegram, Discord, ntfy, etc.) are a deliberate non-goal here.
    """
    user = await set_webhook_url(session, user, body.webhook_url)
    logger.info(
        "webhook_url_updated", user_id=user.id, cleared=body.webhook_url is None
    )
    return UserOut.model_validate(user)
