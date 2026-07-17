from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.user import User, UserRole


class EmailAlreadyRegisteredError(Exception):
    pass


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email.lower()))
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: str) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession, *, email: str, password: str, role: UserRole = UserRole.USER
) -> User:
    email = email.lower()
    existing = await get_user_by_email(session, email)
    if existing is not None:
        raise EmailAlreadyRegisteredError(email)
    user = User(email=email, password_hash=hash_password(password), role=role)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def authenticate_user(
    session: AsyncSession, *, email: str, password: str
) -> User | None:
    user = await get_user_by_email(session, email)
    if user is None:
        # Still run a hash verification against a dummy hash so response timing does not
        # reveal whether the email exists (mitigates a user-enumeration timing side-channel).
        verify_password(
            password, "$argon2id$v=19$m=65536,t=3,p=4$" + "0" * 22 + "$" + "0" * 43
        )
        return None
    if not verify_password(password, user.password_hash):
        return None
    if not user.is_active:
        return None
    return user


async def bootstrap_admin(
    session: AsyncSession, *, email: str | None, password: str | None
) -> User | None:
    """Create the single admin account on first boot if configured and not already present."""
    if not email or not password:
        return None
    existing = await get_user_by_email(session, email)
    if existing is not None:
        return existing
    return await create_user(
        session, email=email, password=password, role=UserRole.ADMIN
    )
