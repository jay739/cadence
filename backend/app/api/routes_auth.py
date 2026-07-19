from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.config import Settings, get_settings
from app.core.limiter import AUTH_RATE_LIMIT, limiter
from app.core.security import create_session_token
from app.models.user import User, UserRole
from app.schemas.user import LoginIn, RegisterIn, TokenOut, UserOut
from app.services.users import (
    EmailAlreadyRegisteredError,
    authenticate_user,
    create_user,
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
    user = await authenticate_user(session, email=body.email, password=body.password)
    if user is None:
        logger.info("login_failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    logger.info("login_succeeded", user_id=user.id)
    return _issue_token_response(response, user, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)
