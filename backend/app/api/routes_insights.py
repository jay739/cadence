"""Admin-only route exposing aggregates over the fulfillment audit trail.

Auth: `require_admin`, the same dependency the integration-settings and approval-queue admin
routes use. A general user must get a 403 here, not a filtered-down view: this reports on every
user's requests, so there is no subset of it that is safely theirs to see.

Read-only. Nothing in this module writes, and it makes no outbound calls of any kind.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_admin
from app.models.user import User
from app.schemas.insights import AdminInsightsOut
from app.services.insights import get_fulfillment_insights

router = APIRouter(prefix="/api/admin/insights", tags=["admin-insights"])

# 365 is a ceiling on the daily series the client has to render (one bar per day), not on what
# the database could answer; a year of bars is already past the point of being readable.
MAX_WINDOW_DAYS = 365
DEFAULT_WINDOW_DAYS = 30


@router.get("", response_model=AdminInsightsOut)
async def get_insights(
    days: int = Query(
        default=DEFAULT_WINDOW_DAYS,
        ge=1,
        le=MAX_WINDOW_DAYS,
        description="How many days back to report on, counting today as day one.",
    ),
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> AdminInsightsOut:
    data = await get_fulfillment_insights(session, days=days)
    # model_validate rather than AdminInsightsOut(**data): the service hands back nested plain
    # dicts (TypedDicts), which Pydantic validates into the nested models here. Splatting them
    # into the constructor works at runtime but is a type error, since a TypedDict is not the
    # BaseModel the field is declared as.
    return AdminInsightsOut.model_validate(data)
