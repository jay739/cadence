from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_current_user, get_db
from app.core.config import Settings
from app.core.limiter import SEARCH_RATE_LIMIT, SEARCH_STREAM_RATE_LIMIT, limiter
from app.models.user import User
from app.schemas.search import MultiSearchResponse
from app.services.search import multi_source_search, stream_source_results

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=MultiSearchResponse)
@limiter.limit(SEARCH_RATE_LIMIT)
async def search_tracks(
    request: Request,
    q: str = Query(..., min_length=1, max_length=300),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> MultiSearchResponse:
    """Search all configured sources in parallel. Requires authentication (not public)."""
    return await multi_source_search(q, settings, session, limit=limit)


@router.get("/stream")
@limiter.limit(SEARCH_STREAM_RATE_LIMIT)
async def search_tracks_stream(
    request: Request,
    q: str = Query(..., min_length=1, max_length=300),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Same four sources as GET /api/search, but as a Server-Sent Events stream: each source's
    result is sent the moment that source finishes, instead of waiting for the slowest of the
    four (MusicBrainz's 1 req/sec rate limit or a slow/misconfigured Prowlarr can otherwise make
    every tab wait on the slowest one). The browser's EventSource API cannot set an
    Authorization header, so this route is reachable via the session cookie set at login
    (get_current_user already falls back to it) rather than only the Bearer token the SPA
    otherwise uses for every other request.
    """

    async def event_stream():
        async for source_result in stream_source_results(
            q, settings, session, limit=limit
        ):
            yield f"data: {source_result.model_dump_json()}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Disables buffering on nginx (the operator's own reverse proxy, per the mission's
            # deployment model) so events actually arrive incrementally rather than all at once
            # once the whole response finally completes.
            "X-Accel-Buffering": "no",
        },
    )
