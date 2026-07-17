"""Seeds a demo user, a demo admin, and a fake request queue covering every RequestStatus.

Only runs when DEMO_MODE=true and the database is empty of requests, so re-running seed on an
already-seeded demo instance is a no-op rather than creating duplicates.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.data import DEMO_REQUEST_SEEDS
from app.models.request import FulfillmentAttempt, FulfillmentOutcome, Request
from app.models.user import User, UserRole
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

    for seed in DEMO_REQUEST_SEEDS:
        dedupe_key = compute_dedupe_key(seed["source"], seed["source_id"])
        row = Request(
            user_id=demo_user.id,
            title=seed["title"],
            artist=seed["artist"],
            album=seed.get("album"),
            source=seed["source"],
            source_id=seed["source_id"],
            dedupe_key=dedupe_key,
            status=seed["status"],
            admin_note=seed.get("admin_note"),
            failure_reason=seed.get("failure_reason"),
            final_file_path=seed.get("final_file_path"),
        )
        session.add(row)
        await session.flush()

        if seed["status"].value in ("downloading", "fulfilled", "failed"):
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
                        if seed["status"].value != "failed"
                        else None
                    ),
                    outcome=(
                        FulfillmentOutcome.GRABBED.value
                        if seed["status"].value != "failed"
                        else FulfillmentOutcome.SEARCHED_NO_QUALIFYING_CANDIDATE.value
                    ),
                    detail=seed.get("failure_reason"),
                )
            )
            row.fulfillment_attempts_count = 1

    await session.commit()
    logger.info("demo_data_seeded", requests=len(DEMO_REQUEST_SEEDS))
