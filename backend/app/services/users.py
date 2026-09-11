from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.user import AuthProvider, User, UserRole
from app.services.navidrome_auth import NavidromeAuthenticator, NavidromeProfile


class EmailAlreadyRegisteredError(Exception):
    pass


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email.lower()))
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: str) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def set_webhook_url(
    session: AsyncSession, user: User, webhook_url: str | None
) -> User:
    """Set or clear the caller's own webhook URL. Format is already validated by the schema
    (WebhookUrlIn) before this is called; this function just persists it."""
    user.webhook_url = webhook_url
    await session.commit()
    await session.refresh(user)
    return user


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
    # An externally-authenticated row has no local hash to check, so it is not a local
    # credential and must take the same timing-equalised path as a missing user. Verifying
    # against a null hash would raise, and short-circuiting without the dummy verify would
    # leak which accounts are Navidrome-backed.
    if (
        user is None
        or user.auth_provider is not AuthProvider.LOCAL
        or not user.password_hash
    ):
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


def _provider_namespaced_email(username: str) -> str:
    """A stand-in address for a Navidrome user whose account carries no email.

    `users.email` is the app's identity column and is NOT NULL, while Navidrome usernames are
    not addresses and its email field is frequently blank. Namespacing to a reserved-looking
    domain keeps the column honest without inventing something that could collide with, or be
    mistaken for, a real address. Making `email` nullable is the better long-term fix.
    """
    return f"{username.lower()}@navidrome.local"


async def get_user_by_external_username(
    session: AsyncSession, username: str
) -> User | None:
    result = await session.execute(
        select(User).where(
            User.external_username == username.lower(),
            User.auth_provider == AuthProvider.NAVIDROME,
        )
    )
    return result.scalar_one_or_none()


async def provision_navidrome_user(
    session: AsyncSession, *, username: str, profile: NavidromeProfile
) -> User:
    """Create or refresh the local row mirroring a Navidrome account.

    Navidrome stays the source of truth for role and email, so a promotion or a corrected
    address there takes effect on the user's next login instead of drifting permanently.
    """
    username = username.lower()
    role = UserRole.ADMIN if profile.is_admin else UserRole.USER
    user = await get_user_by_external_username(session, username)

    if user is not None:
        user.role = role
        if profile.email and profile.email.lower() != user.email:
            clash = await get_user_by_email(session, profile.email)
            if clash is None:
                user.email = profile.email.lower()
        await session.commit()
        await session.refresh(user)
        return user

    email = (profile.email or _provider_namespaced_email(username)).lower()
    if await get_user_by_email(session, email) is not None:
        # A local account already owns that address. Never merge the two: that would hand the
        # Navidrome user someone else's request history and role. Fall back to the namespaced
        # form, which cannot be claimed by an ordinary registration.
        email = _provider_namespaced_email(username)

    user = User(
        email=email,
        password_hash=None,
        auth_provider=AuthProvider.NAVIDROME,
        external_username=username,
        role=role,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def authenticate(
    session: AsyncSession,
    *,
    identifier: str,
    password: str,
    navidrome_url: str | None,
    authenticator: NavidromeAuthenticator | None = None,
) -> User | None:
    """Authenticate against local accounts first, then Navidrome.

    The order is deliberate. The operator's local admin has to keep working when Navidrome is
    down or misconfigured, which is precisely when they need to get in and fix it.
    """
    user = await authenticate_user(session, email=identifier, password=password)
    if user is not None:
        return user

    auth = authenticator or NavidromeAuthenticator(navidrome_url)
    if not auth.configured:
        return None

    # Only an explicit True is an accepted credential. None means Navidrome could not be
    # reached, and must not be reported to the user as a wrong password.
    if await auth.verify_credentials(identifier, password) is not True:
        return None

    profile = await auth.fetch_profile(identifier, password)
    user = await provision_navidrome_user(session, username=identifier, profile=profile)
    return user if user.is_active else None
