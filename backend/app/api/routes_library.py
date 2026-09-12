"""Already-in-library lookups against Navidrome.

Deliberately a separate router from /api/search rather than a field on the search response.
Search fans out to third-party sources and is the app's core function; the library check talks
to an optional local service and is a decoration on top. Keeping them apart means the check
cannot delay, fail, or complicate a search -- the worst it can do is return `available: false`,
at which point the UI shows no badges and everything else behaves exactly as before.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request

from app.api.deps import get_app_settings, get_current_user
from app.core.config import Settings
from app.core.limiter import LIBRARY_CHECK_RATE_LIMIT, limiter
from app.models.user import User
from app.schemas.library import (
    LibraryCheckRequest,
    LibraryCheckResponse,
    LibraryMatch,
)
from app.services.navidrome_library import LibraryTrackQuery, NavidromeLibraryClient

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/library", tags=["library"])


@router.post("/check", response_model=LibraryCheckResponse)
@limiter.limit(LIBRARY_CHECK_RATE_LIMIT)
async def check_library(
    request: Request,
    body: LibraryCheckRequest,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> LibraryCheckResponse:
    """Report which of the supplied tracks are already in the Navidrome library.

    Never raises for an integration problem. An unconfigured service account, an unreachable
    Navidrome, or a malformed answer all resolve to `available: false` with no matches, because
    the honest answer in each case is "unknown" and the caller is told to render nothing.
    """
    # Demo mode has no Navidrome behind it, and inventing plausible-looking library hits would
    # be exactly the false positive this feature is built to avoid.
    if settings.demo_mode or not settings.navidrome_library_configured:
        return LibraryCheckResponse(available=False, matches=[])

    client = NavidromeLibraryClient(
        settings.navidrome_url,
        settings.navidrome_admin_user,
        settings.navidrome_admin_password,
    )
    queries = [
        LibraryTrackQuery(key=item.key, title=item.title, artist=item.artist)
        for item in body.tracks
    ]
    result = await client.check(queries)

    return LibraryCheckResponse(
        available=result.available,
        matches=[
            LibraryMatch(
                key=key,
                navidrome_id=hit.navidrome_id,
                title=hit.title,
                artist=hit.artist,
                album=hit.album,
            )
            for key, hit in result.hits.items()
        ],
    )
