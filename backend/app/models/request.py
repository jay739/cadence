from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class RequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DOWNLOADING = "downloading"
    FULFILLED = "fulfilled"
    REJECTED = "rejected"
    FAILED = "failed"


class Request(Base):
    """A single-track request.

    Cadence's non-negotiable premise: one Request always maps to exactly one recording. There
    is no album/artist-level equivalent of this model anywhere in the schema, on purpose.
    """

    __tablename__ = "requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )

    # Track identity captured at request time, used verbatim to drive fulfillment search.
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    artist: Mapped[str] = mapped_column(String(500), nullable=False)
    album: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    artwork_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Provenance of the picked search result, for de-duplication and display (source badge).
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Canonical tagging metadata, filled in once available (MusicBrainz preferred).
    musicbrainz_recording_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus, native_enum=False, length=16),
        default=RequestStatus.PENDING,
        nullable=False,
        index=True,
    )
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_file_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Bookkeeping for idempotency and worker crash-recovery.
    dedupe_key: Mapped[str] = mapped_column(String(600), index=True, nullable=False)
    fulfillment_attempts_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    worker_lock_token: Mapped[str | None] = mapped_column(String(36), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    user: Mapped["User"] = relationship(back_populates="requests")
    attempts: Mapped[list["FulfillmentAttempt"]] = relationship(
        back_populates="request",
        order_by="FulfillmentAttempt.created_at",
        cascade="all, delete-orphan",
    )


class FulfillmentAttempt(Base):
    """One structured, retrievable audit-trail entry for a fulfillment step.

    Every search performed, every candidate considered, and the decision made (or the reason
    nothing qualified) is recorded here per request, not just in application logs.
    """

    __tablename__ = "fulfillment_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("requests.id"), nullable=False
    )

    backend: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # "prowlarr" | "slskd"
    query_used: Mapped[str] = mapped_column(String(600), nullable=False)
    candidates_considered: Mapped[list | None] = mapped_column(JSON, nullable=True)
    chosen_candidate: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    outcome: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # see FulfillmentOutcome
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )

    request: Mapped["Request"] = relationship(back_populates="attempts")


class FulfillmentOutcome(str, enum.Enum):
    SEARCHED_NO_CANDIDATES = "searched_no_candidates"
    SEARCHED_NO_QUALIFYING_CANDIDATE = "searched_no_qualifying_candidate"
    GRABBED = "grabbed"
    DOWNLOAD_STARTED = "download_started"
    DOWNLOAD_COMPLETED = "download_completed"
    DOWNLOAD_FAILED = "download_failed"
    ERROR = "error"
    SKIPPED_NOT_CONFIGURED = "skipped_not_configured"
