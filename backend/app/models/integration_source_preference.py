"""Per-integration choice of which source wins: the environment, or the database.

Addresses issue #18: the precedence rule in `app.services.settings_store` is otherwise
hardcoded (env always wins when set). An operator who wants to manage an integration entirely
from the settings UI cannot, because a leftover env var silently overrides whatever they save.
This table lets that be flipped per integration, while `Settings.settings_force_env_precedence`
(see app.core.config) retains a break-glass path that ignores every row here and forces env-wins
everywhere, in case a bad database value ever locks an operator out of the settings UI itself.

One row per integration, created lazily on first write; a missing row means "env" (unchanged
default behavior for every integration that has never touched this feature).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class IntegrationSourcePreference(Base):
    __tablename__ = "integration_source_preferences"

    # The integration name itself is the primary key: exactly one preference per integration.
    integration: Mapped[str] = mapped_column(String(32), primary_key=True)
    # "env" or "database". Anything else is a programming error, not user input (validated at
    # the API boundary in app.schemas.settings), so this stays a plain String rather than a
    # DB-level enum -- consistent with IntegrationSetting's own "no column-level typing" choice.
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="env")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
