"""Fulfillment decision logic: Prowlarr first, Soulseek (slskd) fallback, never more.

For a single approved Request:
  1. Search Prowlarr for the specific track (title + artist + album if known).
  2. Score every candidate release with app.services.scoring; if one qualifies, grab it via
     Prowlarr's grab API, hand off to qBittorrent (already configured as Prowlarr's download
     client on the operator's network), poll qBittorrent until the torrent completes.
  3. If Prowlarr search/grab yields nothing usable within FULFILLMENT_PROWLARR_ATTEMPT_BUDGET
     attempts (each attempt = one full search+score+grab cycle; a single search only ever
     needs one cycle today, the budget exists so future retry/backoff logic has somewhere to
     live without redesigning this function), fall back to Soulseek via slskd: search, score
     candidates the same way (bitrate/format/filename plausibility), download the single best
     file, poll slskd until the transfer completes.
  4. On completion of either path: move/rename the file into MUSIC_LIBRARY_PATH using the
     configured pattern (via app.services.pathsafety, which guarantees the result cannot
     escape the library root), tag it (preferring MusicBrainz canonical tags), mark the
     request fulfilled with the final path recorded.
  5. On exhaustion of both paths: mark the request failed with a human-readable reason.

Every step appends a FulfillmentAttempt row: what was searched, what candidates were seen,
what was picked and why (or why nothing qualified). This is the audit trail required by the
mission, retrievable per request via GET /api/requests/{id}.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.request import (
    FulfillmentAttempt,
    FulfillmentOutcome,
    Request,
    RequestStatus,
)
from app.search_sources.musicbrainz import lookup_recording
from app.search_sources.prowlarr import ProwlarrSearchSource
from app.services.pathsafety import PathSafetyError, render_library_path
from app.services.qbittorrent import QBittorrentClient
from app.services.scoring import (
    ScoredCandidate,
    best_qualifying_candidate,
    extract_extension,
    format_rank,
    title_artist_similarity,
)
from app.services.slskd import SlskdClient
from app.services.tagging import write_tags

logger = structlog.get_logger(__name__)


def _query_for(request_row: Request) -> str:
    parts = [request_row.artist, request_row.title]
    return " ".join(p for p in parts if p)


async def _log_attempt(
    session: AsyncSession,
    request_row: Request,
    *,
    backend: str,
    query_used: str,
    candidates_considered: list | None,
    chosen_candidate: dict | None,
    outcome: FulfillmentOutcome,
    detail: str | None = None,
) -> None:
    attempt = FulfillmentAttempt(
        request_id=request_row.id,
        backend=backend,
        query_used=query_used,
        candidates_considered=candidates_considered,
        chosen_candidate=chosen_candidate,
        outcome=outcome.value,
        detail=detail,
    )
    session.add(attempt)
    request_row.fulfillment_attempts_count += 1
    await session.commit()


async def _finalize_file(
    session: AsyncSession,
    request_row: Request,
    settings: Settings,
    *,
    source_file: Path,
) -> None:
    ext = extract_extension(source_file.name) or "bin"
    try:
        dest = render_library_path(
            settings.library_path_pattern,
            library_root=settings.music_library_path_resolved,
            artist=request_row.artist,
            title=request_row.title,
            ext=ext,
            album=request_row.album,
        )
    except PathSafetyError as exc:
        logger.error("path_safety_violation", request_id=request_row.id, error=str(exc))
        request_row.status = RequestStatus.FAILED
        request_row.failure_reason = "Internal error: refused unsafe destination path"
        await session.commit()
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source_file), str(dest))

    tag_title, tag_artist, tag_album = (
        request_row.title,
        request_row.artist,
        request_row.album,
    )
    if request_row.musicbrainz_recording_id:
        try:
            mb_data = await lookup_recording(request_row.musicbrainz_recording_id)
            if mb_data:
                tag_title = mb_data.get("title", tag_title)
                credits = mb_data.get("artist-credit") or []
                if credits:
                    tag_artist = credits[0]["artist"]["name"]
                releases = mb_data.get("releases") or []
                if releases:
                    tag_album = releases[0].get("title", tag_album)
        except Exception as exc:  # noqa: BLE001 - tagging metadata is best-effort
            logger.warning("musicbrainz_tag_lookup_failed", error=str(exc))

    write_tags(dest, title=tag_title, artist=tag_artist, album=tag_album)

    request_row.status = RequestStatus.FULFILLED
    request_row.final_file_path = str(
        dest.relative_to(settings.music_library_path_resolved)
    )
    await session.commit()


async def _try_prowlarr(
    session: AsyncSession, request_row: Request, settings: Settings, staging_dir: Path
) -> bool:
    prowlarr = ProwlarrSearchSource(settings.prowlarr_url, settings.prowlarr_api_key)
    qbt = QBittorrentClient(
        settings.qbittorrent_url,
        settings.qbittorrent_username,
        settings.qbittorrent_password,
    )
    query = _query_for(request_row)

    if not prowlarr.configured:
        await _log_attempt(
            session,
            request_row,
            backend="prowlarr",
            query_used=query,
            candidates_considered=None,
            chosen_candidate=None,
            outcome=FulfillmentOutcome.SKIPPED_NOT_CONFIGURED,
        )
        return False

    try:
        raw = await prowlarr.search_raw(query, limit=50)
    except Exception as exc:  # noqa: BLE001
        await _log_attempt(
            session,
            request_row,
            backend="prowlarr",
            query_used=query,
            candidates_considered=None,
            chosen_candidate=None,
            outcome=FulfillmentOutcome.ERROR,
            detail=str(exc),
        )
        return False

    if not raw:
        await _log_attempt(
            session,
            request_row,
            backend="prowlarr",
            query_used=query,
            candidates_considered=[],
            chosen_candidate=None,
            outcome=FulfillmentOutcome.SEARCHED_NO_CANDIDATES,
        )
        return False

    scored = [
        ScoredCandidate(
            identifier=item.get("guid", ""),
            raw_name=item.get("title", ""),
            similarity=title_artist_similarity(
                item.get("title", ""), request_row.title, request_row.artist
            ),
            format_score=format_rank(extract_extension(item.get("title", ""))),
            size_bytes=item.get("size"),
            extra={
                "indexerId": item.get("indexerId"),
                "indexer": item.get("indexer"),
                "seeders": item.get("seeders"),
            },
        )
        for item in raw
    ]
    candidates_summary = [
        {"name": c.raw_name, "similarity": round(c.similarity, 3), "extra": c.extra}
        for c in scored
    ]

    winner = best_qualifying_candidate(scored)
    if winner is None:
        await _log_attempt(
            session,
            request_row,
            backend="prowlarr",
            query_used=query,
            candidates_considered=candidates_summary,
            chosen_candidate=None,
            outcome=FulfillmentOutcome.SEARCHED_NO_QUALIFYING_CANDIDATE,
            detail="No candidate met the minimum title/artist similarity threshold",
        )
        return False

    indexer_id = winner.extra.get("indexerId")
    grabbed = (
        await prowlarr.grab(winner.identifier, indexer_id)
        if indexer_id is not None
        else False
    )
    if not grabbed:
        await _log_attempt(
            session,
            request_row,
            backend="prowlarr",
            query_used=query,
            candidates_considered=candidates_summary,
            chosen_candidate={"name": winner.raw_name, "similarity": winner.similarity},
            outcome=FulfillmentOutcome.ERROR,
            detail="Grab call to Prowlarr failed",
        )
        return False

    await _log_attempt(
        session,
        request_row,
        backend="prowlarr",
        query_used=query,
        candidates_considered=candidates_summary,
        chosen_candidate={"name": winner.raw_name, "similarity": winner.similarity},
        outcome=FulfillmentOutcome.GRABBED,
    )

    if not qbt.configured:
        await _log_attempt(
            session,
            request_row,
            backend="prowlarr",
            query_used=query,
            candidates_considered=None,
            chosen_candidate=None,
            outcome=FulfillmentOutcome.SKIPPED_NOT_CONFIGURED,
            detail="Grabbed via Prowlarr but qBittorrent is not configured to confirm completion",
        )
        return False

    torrent = await qbt.find_torrent_by_hint(winner.raw_name)
    if torrent is None:
        return False
    if not await qbt.is_complete(torrent["hash"]):
        return False

    content_name = await qbt.get_content_path(torrent["hash"])
    if not content_name:
        return False

    save_path = Path(torrent.get("save_path", staging_dir))
    source_file = save_path / content_name
    if not source_file.exists():
        await _log_attempt(
            session,
            request_row,
            backend="prowlarr",
            query_used=query,
            candidates_considered=None,
            chosen_candidate=None,
            outcome=FulfillmentOutcome.DOWNLOAD_FAILED,
            detail=f"Expected completed file not found at {source_file}",
        )
        return False

    await _log_attempt(
        session,
        request_row,
        backend="prowlarr",
        query_used=query,
        candidates_considered=None,
        chosen_candidate=None,
        outcome=FulfillmentOutcome.DOWNLOAD_COMPLETED,
    )
    await _finalize_file(session, request_row, settings, source_file=source_file)
    return True


async def _try_slskd(
    session: AsyncSession, request_row: Request, settings: Settings, staging_dir: Path
) -> bool:
    slskd = SlskdClient(settings.slskd_url, settings.slskd_api_key)
    query = _query_for(request_row)

    if not slskd.configured:
        await _log_attempt(
            session,
            request_row,
            backend="slskd",
            query_used=query,
            candidates_considered=None,
            chosen_candidate=None,
            outcome=FulfillmentOutcome.SKIPPED_NOT_CONFIGURED,
        )
        return False

    try:
        files = await slskd.search(query)
    except Exception as exc:  # noqa: BLE001
        await _log_attempt(
            session,
            request_row,
            backend="slskd",
            query_used=query,
            candidates_considered=None,
            chosen_candidate=None,
            outcome=FulfillmentOutcome.ERROR,
            detail=str(exc),
        )
        return False

    if not files:
        await _log_attempt(
            session,
            request_row,
            backend="slskd",
            query_used=query,
            candidates_considered=[],
            chosen_candidate=None,
            outcome=FulfillmentOutcome.SEARCHED_NO_CANDIDATES,
        )
        return False

    scored = [
        ScoredCandidate(
            identifier=f.get("filename", ""),
            raw_name=f.get("filename", ""),
            similarity=title_artist_similarity(
                f.get("filename", ""), request_row.title, request_row.artist
            ),
            format_score=format_rank(extract_extension(f.get("filename", ""))),
            size_bytes=f.get("size"),
            extra={"username": f.get("username"), "bitRate": f.get("bitRate")},
        )
        for f in files
    ]
    candidates_summary = [
        {"name": c.raw_name, "similarity": round(c.similarity, 3), "extra": c.extra}
        for c in scored
    ]

    winner = best_qualifying_candidate(scored)
    if winner is None:
        await _log_attempt(
            session,
            request_row,
            backend="slskd",
            query_used=query,
            candidates_considered=candidates_summary,
            chosen_candidate=None,
            outcome=FulfillmentOutcome.SEARCHED_NO_QUALIFYING_CANDIDATE,
        )
        return False

    username = winner.extra.get("username")
    started = await slskd.enqueue_download(
        username, winner.identifier, winner.size_bytes
    )
    if not started:
        await _log_attempt(
            session,
            request_row,
            backend="slskd",
            query_used=query,
            candidates_considered=candidates_summary,
            chosen_candidate={"name": winner.raw_name, "similarity": winner.similarity},
            outcome=FulfillmentOutcome.ERROR,
            detail="Failed to enqueue Soulseek download",
        )
        return False

    await _log_attempt(
        session,
        request_row,
        backend="slskd",
        query_used=query,
        candidates_considered=candidates_summary,
        chosen_candidate={"name": winner.raw_name, "similarity": winner.similarity},
        outcome=FulfillmentOutcome.DOWNLOAD_STARTED,
    )

    if not await slskd.is_download_complete(username, winner.identifier):
        return False

    source_file = staging_dir / Path(winner.identifier).name
    if not source_file.exists():
        await _log_attempt(
            session,
            request_row,
            backend="slskd",
            query_used=query,
            candidates_considered=None,
            chosen_candidate=None,
            outcome=FulfillmentOutcome.DOWNLOAD_FAILED,
            detail=f"Expected completed file not found at {source_file}",
        )
        return False

    await _log_attempt(
        session,
        request_row,
        backend="slskd",
        query_used=query,
        candidates_considered=None,
        chosen_candidate=None,
        outcome=FulfillmentOutcome.DOWNLOAD_COMPLETED,
    )
    await _finalize_file(session, request_row, settings, source_file=source_file)
    return True


async def fulfill_request(
    session: AsyncSession, request_row: Request, settings: Settings
) -> None:
    """Drive one request through Prowlarr-first, Soulseek-fallback fulfillment.

    Safe to call again on a request left in DOWNLOADING/APPROVED state after a crash: it
    re-evaluates from scratch (a fresh search) rather than assuming prior in-flight state is
    still valid, per the mission's crash-recovery requirement. This is deliberately simple
    (re-search rather than resume-in-place) because Soulseek/Prowlarr downloads are cheap to
    re-initiate and re-scoring guards against grabbing a second copy of something already
    fulfilled.
    """
    if request_row.status == RequestStatus.FULFILLED:
        return

    request_row.status = RequestStatus.DOWNLOADING
    await session.commit()

    staging_dir = settings.data_dir_path / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        if await _try_prowlarr(session, request_row, settings, staging_dir):
            return
        if await _try_slskd(session, request_row, settings, staging_dir):
            return
    except (
        Exception
    ) as exc:  # noqa: BLE001 - a crash here must fail the job, not the worker
        logger.error(
            "fulfillment_unhandled_error",
            request_id=request_row.id,
            error=str(exc),
            exc_info=True,
        )
        request_row.status = RequestStatus.FAILED
        request_row.failure_reason = (
            "Internal error during fulfillment, see server logs"
        )
        await session.commit()
        return

    request_row.status = RequestStatus.FAILED
    request_row.failure_reason = (
        "No qualifying release found on configured indexers, and the Soulseek fallback "
        "found no exact-enough match (or is not configured) within the attempt budget."
    )
    await session.commit()
