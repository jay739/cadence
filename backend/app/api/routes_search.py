from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_app_settings, get_current_user
from app.core.config import Settings
from app.core.limiter import SEARCH_RATE_LIMIT, limiter
from app.models.user import User
from app.schemas.search import MultiSearchResponse
from app.services.search import multi_source_search

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=MultiSearchResponse)
@limiter.limit(SEARCH_RATE_LIMIT)
async def search_tracks(
    request: Request,
    q: str = Query(..., min_length=1, max_length=300),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> MultiSearchResponse:
    """Search all configured sources in parallel. Requires authentication (not public)."""
    return await multi_source_search(q, settings, limit=limit)
