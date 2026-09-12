from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.config import Settings, get_settings
from app.core.limiter import auth_rate_limit, limiter
from app.core.security import create_session_token
from app.models.user import AuthProvider, User, UserRole
from app.schemas.user import LoginIn, RegisterIn, TokenOut, UserOut, WebhookUrlIn
from app.services.avatars import resolve_avatar
from app.services.users import (
    EmailAlreadyRegisteredError,
    authenticate,
    create_user,
    set_webhook_url,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "cadence_session"


def build_user_out(user: User, settings: Settings) -> UserOut:
    """Shared "current user" serializer, used by every route (register/login/me, and the
    profile routes in app.api.routes_users) that needs to hand the client a UserOut.

    `navidrome_url` cannot come from `model_validate`'s from_attributes lookup (it is not a
    column on User), so it is filled in explicitly here, and only for a Navidrome-backed
    account -- this is what the frontend's profile page links to for password changes, since
    Cadence never stores a password for these accounts to change itself.

    `avatar`/`avatar_source` are resolved the same way and for the same reason. The seed is the
    user's id rather than their email precisely because the email is the thing a later change is
    expected to replace: seeding on it would silently regenerate every doodle the day that
    lands. `navidrome_picture_url` is left unset because Navidrome has no per-user avatar to
    fetch -- app.services.avatars records the probe that established that.
    """
    out = UserOut.model_validate(user)
    if user.auth_provider is AuthProvider.NAVIDROME:
        out.navidrome_url = settings.navidrome_url
    avatar = resolve_avatar(seed=user.id, avatar_url=user.avatar_url)
    out.avatar = avatar.url
    out.avatar_source = avatar.source
    return out


def issue_token_response(
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
    return TokenOut(access_token=token, user=build_user_out(user, settings))


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
@limiter.limit(auth_rate_limit)
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
    return issue_token_response(response, user, settings)


@router.post("/login", response_model=TokenOut)
@limiter.limit(auth_rate_limit)
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
    return issue_token_response(response, user, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=UserOut)
async def me(
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> UserOut:
    return build_user_out(user, settings)


@router.patch("/me/webhook", response_model=UserOut)
async def update_webhook_url(
    body: WebhookUrlIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
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
    return build_user_out(user, settings)
