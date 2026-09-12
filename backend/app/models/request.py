from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


class RequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DOWNLOADING = "downloading"
    FULFILLED = "fulfilled"
    REJECTED = "rejected"
    FAILED = "failed"
    # Not a failure and not a success: every tier was searched, matches for this exact track
    # were found, and none of them were lossless. Delivering one of those lossy matches anyway
    # would be a silent downgrade of what was asked for, and failing outright would throw away
    # a file the requester may well be happy with, so the request parks here until the
    # requester answers "accept a lossy copy?" (see app.services.requests.accept_lossy_copy).
    #
    # SQLAlchemy persists the enum's *name*, not its value, into a VARCHAR(16) column, so this
    # member's name is exactly 16 characters by necessity, not by coincidence.
    AWAITING_QUALITY = "awaiting_quality"


class Request(Base):
    """A single-track request.

    Cadence's non-negotiable premise: one Request always maps to exactly one recording. There
    is no album/artist-level equivalent of this model anywhere in the schema, on purpose.
    """

    __tablename__ = "requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Indexed: filtered on every "my requests" list load and every dedupe check on request
    # creation (SQLite does not automatically index foreign key columns).
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
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

    # The requester's audio-quality ask ("any" | "lossy" | "lossless"), deliberately generic
    # rather than naming a container: it maps onto what the fulfillment tiers can actually
    # promise, where a specific codec does not (YouTube can never hand back FLAC no matter how
    # the ask is phrased). "any" and "lossy" are reordering signals only, never a hard filter;
    # "lossless" IS a hard filter, because handing over a lossy file to someone who asked for
    # lossless is a silent downgrade, not a near-enough match. server_default keeps rows from
    # before this column (and any writer that doesn't set it) on the neutral value.
    desired_quality: Mapped[str] = mapped_column(
        String(16), nullable=False, default="any", server_default="any"
    )

    # Set only by the requester's own explicit "a lossy copy is fine" answer to an
    # AWAITING_QUALITY request. Kept as its own flag rather than rewriting desired_quality:
    # what was originally asked for stays on the record, and the answer to the question is
    # visibly a separate, later decision.
    lossy_accepted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
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

    user: Mapped[User] = relationship(back_populates="requests")
    attempts: Mapped[list[FulfillmentAttempt]] = relationship(
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
    # Indexed: the request detail page's lazy-loaded `attempts` relationship filters on this
    # (SQLite does not automatically index foreign key columns).
    request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("requests.id"), nullable=False, index=True
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

    request: Mapped[Request] = relationship(back_populates="attempts")


class FulfillmentOutcome(str, enum.Enum):
    SEARCHED_NO_CANDIDATES = "searched_no_candidates"
    SEARCHED_NO_QUALIFYING_CANDIDATE = "searched_no_qualifying_candidate"
    # This tier found the right track but could only offer it lossy, against a request that
    # asked for lossless. Distinct from SEARCHED_NO_QUALIFYING_CANDIDATE on purpose: "we found
    # it, just not in the quality you asked for" is what makes asking the requester worthwhile,
    # where "nothing here matches this song" does not.
    DECLINED_LOSSY_ONLY = "declined_lossy_only"
    GRABBED = "grabbed"
    DOWNLOAD_STARTED = "download_started"
    DOWNLOAD_COMPLETED = "download_completed"
    DOWNLOAD_FAILED = "download_failed"
    ERROR = "error"
    SKIPPED_NOT_CONFIGURED = "skipped_not_configured"
