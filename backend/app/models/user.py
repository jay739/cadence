from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.request import Request


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


class UserRole(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"


class AuthProvider(str, enum.Enum):
    """Where this account's password actually lives.

    LOCAL rows own an argon2 hash. NAVIDROME rows never store a password in any form: the
    credential is verified against Navidrome on every login, so password_hash stays null.
    """

    LOCAL = "local"
    NAVIDROME = "navidrome"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(
        String(320), unique=True, index=True, nullable=False
    )
    # Null for externally-authenticated accounts, which have no password here to hash.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_provider: Mapped[AuthProvider] = mapped_column(
        Enum(AuthProvider, native_enum=False, length=16),
        default=AuthProvider.LOCAL,
        server_default=AuthProvider.LOCAL.value,
        nullable=False,
    )
    # The username as Navidrome knows it, which is not necessarily an email address and is
    # what gets replayed to Navidrome on each login. Null for local accounts.
    external_username: Mapped[str | None] = mapped_column(
        String(255), index=True, nullable=True
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, length=16),
        default=UserRole.USER,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # Session/token rotation: bumping this invalidates all previously issued session tokens.
    token_version: Mapped[int] = mapped_column(default=0, nullable=False)

    # Optional per-user Spotify personalization link (Authorization Code flow, opt-in).
    spotify_access_token: Mapped[str | None] = mapped_column(
        String(2048), nullable=True
    )
    spotify_refresh_token: Mapped[str | None] = mapped_column(
        String(2048), nullable=True
    )
    spotify_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    requests: Mapped[list[Request]] = relationship(back_populates="user")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<User {self.email} role={self.role}>"
