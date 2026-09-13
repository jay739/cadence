"""Seeds a demo user, a demo admin, and a fake request queue covering every RequestStatus.

Only runs when DEMO_MODE=true and the database is empty of requests, so re-running seed on an
already-seeded demo instance is a no-op rather than creating duplicates.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.data import DEMO_REQUEST_SEEDS
from app.models.request import (
    FulfillmentAttempt,
    FulfillmentOutcome,
    Request,
    RequestStatus,
)
from app.models.user import UserRole
from app.services.requests import compute_dedupe_key
from app.services.users import create_user, get_user_by_email

logger = structlog.get_logger(__name__)

DEMO_ADMIN_EMAIL = "admin@demo.cadence.internal"
DEMO_ADMIN_PASSWORD = (
    "demo-admin-password-not-real"  # noqa: S105 - demo-mode only, documented
)
DEMO_USER_EMAIL = "listener@demo.cadence.internal"
DEMO_USER_PASSWORD = (
    "demo-user-password-not-real"  # noqa: S105 - demo-mode only, documented
)


async def seed_demo_data(session: AsyncSession) -> None:
    existing = await session.execute(select(Request).limit(1))
    if existing.scalar_one_or_none() is not None:
        return

    admin = await get_user_by_email(session, DEMO_ADMIN_EMAIL)
    if admin is None:
        admin = await create_user(
            session,
            email=DEMO_ADMIN_EMAIL,
            password=DEMO_ADMIN_PASSWORD,
            role=UserRole.ADMIN,
        )

    demo_user = await get_user_by_email(session, DEMO_USER_EMAIL)
    if demo_user is None:
        demo_user = await create_user(
            session,
            email=DEMO_USER_EMAIL,
            password=DEMO_USER_PASSWORD,
            role=UserRole.USER,
        )

    now = datetime.now(UTC)

    for seed in DEMO_REQUEST_SEEDS:
        status: RequestStatus = seed["status"]
        dedupe_key = compute_dedupe_key(seed["source"], seed["source_id"])
        # Spread across the last week and a half rather than all landing on boot time, so the
        # request-volume series on the admin insight page shows a history instead of one spike.
        # Explicit rather than left to the column default, which would stamp "now" on all of them.
        created_at = now - timedelta(days=seed.get("created_days_ago", 0))
        row = Request(
            user_id=demo_user.id,
            title=seed["title"],
            artist=seed["artist"],
            album=seed.get("album"),
            source=seed["source"],
            source_id=seed["source_id"],
            dedupe_key=dedupe_key,
            status=status,
            admin_note=seed.get("admin_note"),
            failure_reason=seed.get("failure_reason"),
            final_file_path=seed.get("final_file_path"),
            desired_quality=seed.get("desired_quality", "any"),
            created_at=created_at,
            updated_at=created_at,
        )
        session.add(row)
        await session.flush()

        if status is RequestStatus.AWAITING_QUALITY:
            # The audit row that justifies the question the UI is about to ask: this is what a
            # real lossless request looks like after the last tier declined to hand over a
            # lossy file on its own authority.
            session.add(
                FulfillmentAttempt(
                    request_id=row.id,
                    backend="ytdlp",
                    query_used=f"{seed['artist']} {seed['title']}",
                    candidates_considered=[
                        {
                            "name": f"{seed['artist']} - {seed['title']} (Official Video)",
                            "similarity": 0.91,
                        }
                    ],
                    chosen_candidate=None,
                    outcome=FulfillmentOutcome.DECLINED_LOSSY_ONLY.value,
                    detail=(
                        "Found this track on YouTube, but YouTube has no lossless source at "
                        "all: every stream here is Opus or AAC. This tier cannot satisfy a "
                        "lossless request, so it declined rather than handing over a lossy "
                        "file."
                    ),
                    created_at=created_at,
                )
            )
            row.fulfillment_attempts_count = 1

        if status.value in ("downloading", "fulfilled", "failed"):
            session.add(
                FulfillmentAttempt(
                    request_id=row.id,
                    backend="prowlarr",
                    query_used=f"{seed['artist']} {seed['title']}",
                    candidates_considered=[
                        {
                            "name": f"{seed['artist']} - {seed['title']} [FLAC]",
                            "similarity": 0.94,
                        }
                    ],
                    chosen_candidate=(
                        {
                            "name": f"{seed['artist']} - {seed['title']} [FLAC]",
                            "similarity": 0.94,
                        }
                        if status.value != "failed"
                        else None
                    ),
                    outcome=(
                        FulfillmentOutcome.GRABBED.value
                        if status.value != "failed"
                        else FulfillmentOutcome.SEARCHED_NO_QUALIFYING_CANDIDATE.value
                    ),
                    detail=seed.get("failure_reason"),
                    created_at=created_at,
                )
            )
            row.fulfillment_attempts_count = 1

        if status is RequestStatus.FULFILLED:
            # The completion row the real worker writes once the grab above finishes (see the
            # DOWNLOAD_COMPLETED write at the end of _try_prowlarr). Without it the demo's
            # audit trail stops at "handed to the download client" for a request whose whole
            # point is that it landed, and every tier's delivery rate on the admin insight
            # page reads as 0% because nothing in the seed data ever completes.
            session.add(
                FulfillmentAttempt(
                    request_id=row.id,
                    backend="prowlarr",
                    query_used=f"{seed['artist']} {seed['title']}",
                    candidates_considered=None,
                    chosen_candidate=None,
                    outcome=FulfillmentOutcome.DOWNLOAD_COMPLETED.value,
                    detail=f"Filed at {seed.get('final_file_path')}",
                    created_at=created_at,
                )
            )
            row.fulfillment_attempts_count = 2

    await session.commit()
    logger.info("demo_data_seeded", requests=len(DEMO_REQUEST_SEEDS))
