"""Admin-editable, database-backed store for integration connection settings.

Scope is deliberately narrow: only the Prowlarr/qBittorrent/slskd/Spotify/library fields the
mission addendum names. Non-credential tuning fields (SESSION_TTL_MINUTES,
OUTBOUND_TIMEOUT_SECONDS, fulfillment attempt budgets, etc.) stay env-only and are never
represented here.

Shape: one row per (integration, field) pair rather than one row per integration with a fixed
set of columns. Chosen because:
  - Adding a field to an integration later (or a new integration) is a data change, not a
    schema migration.
  - Masking/encryption is a per-field concern (some fields on the same integration are secrets,
    others aren't, e.g. qbittorrent_url vs qbittorrent_password) and this shape makes that an
    explicit per-row flag rather than a naming convention spread across many columns.
  - Partial updates (an admin edits just one field of one integration) map directly onto
    upserting a single row rather than a read-modify-write of a wide row.

The trade-off, accepted deliberately: no per-integration relational grouping enforced by the
schema itself (an integration is just a string tag on each row), and no column-level typing;
every stored `value` is a string. Field-level type coercion (e.g. treating a Spotify client ID
as a string) is trivial since every one of these fields is a string in Settings already, so
this is not a real loss for this specific set of fields.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


class IntegrationSetting(Base):
    """One (integration, field_name) -> value row, value encrypted at rest if is_secret."""

    __tablename__ = "integration_settings"
    __table_args__ = (
        UniqueConstraint("integration", "field_name", name="uq_integration_field"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    integration: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Fernet ciphertext (base64 text) when is_secret is True, plaintext otherwise.
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
