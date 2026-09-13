"""Fulfillment decision logic: Prowlarr, then Soulseek (slskd), then YouTube (ytdlp-shim).

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
  4. If slskd also comes up empty, fall back to YouTube via ytdlp-shim: search, score the same
     way, download the single best match's audio, poll until the job settles. Last resort
     deliberately: the least reliable source (dependent on yt-dlp's extractor keeping pace with
     YouTube's own changes) and never authoritative metadata, but the deepest catalog for the
     regional/long-tail tracks that both other tiers tend to miss entirely.
  5. On completion of any path: file the track into MUSIC_LIBRARY_PATH using the configured
     pattern (via app.services.pathsafety, which guarantees the result cannot escape the
     library root), tag it (preferring MusicBrainz canonical tags), mark the request fulfilled
     with the final path recorded. The Prowlarr path COPIES, because the source is the file
     qBittorrent is still seeding and moving it errors the torrent; slskd and ytdlp-shim both
     MOVE, because their source is Cadence's own staging dir and leaving files there leaks disk.
  6. On exhaustion of every path: mark the request failed with a human-readable reason -- with
     one exception. A request that asked for lossless, where every tier found the track but
     only ever as lossy audio (always the case once fulfillment reaches YouTube, which has no
     lossless source at all), ends in AWAITING_QUALITY instead: neither silently downgraded to
     a lossy file nor written off as a failure, but parked on an explicit question to the
     requester, resumable via app.services.requests.accept_lossy_copy.

Every step appends a FulfillmentAttempt row: what was searched, what candidates were seen,
what was picked and why (or why nothing qualified). This is the audit trail required by the
mission, retrievable per request via GET /api/requests/{id}.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
from app.models.user import User
from app.search_sources.prowlarr import ProwlarrSearchSource
from app.services.disk_space import insufficient_space_reason
from app.services.pathsafety import PathSafetyError, render_library_path
from app.services.qbittorrent import QBittorrentClient
from app.services.scoring import (
    QUALITY_LOSSY,
    ScoredCandidate,
    candidate_quality,
    extract_extension,
    format_rank,
    matches_desired_quality,
    qualifying_candidates_ranked,
    title_artist_similarity,
)
from app.services.settings_store import apply_effective_settings
from app.services.slskd import SlskdClient
from app.services.tagging import collect_track_tags, write_tags
from app.services.webhook_delivery import notify_webhook
from app.services.ytdlp_shim import YtdlpShimClient

logger = structlog.get_logger(__name__)


@dataclass
class _TierResult:
    """What one fulfillment tier did, beyond "did it work".

    `lossy_only_matches` is the signal the whole explicit-confirmation flow hangs off: this
    tier found the requested track and could only offer it lossy, against a request that asked
    for lossless. That is what separates a request worth asking the requester about ("we found
    it, just not lossless") from one that simply failed ("nobody has this track").
    """

    fulfilled: bool
    lossy_only_matches: bool = False


def _requires_lossless(request_row: Request) -> bool:
    """Whether this request's quality ask is currently a hard filter on candidates.

    Only "lossless" is ever a hard filter, and only until the requester has agreed to settle:
    once `lossy_accepted` is set, lossless goes back to being the same reordering preference
    "lossy" always was, so the retry still prefers a lossless copy without refusing anything.
    """
    return request_row.desired_quality == "lossless" and not request_row.lossy_accepted


def _quality_ask_for_shim(request_row: Request) -> str:
    """The quality this request still insists on, as the ytdlp-shim's own API understands it.

    A requester who has accepted a lossy copy is asking for "any" from here on, so the shim
    must not be handed the original "lossless" and refuse a job the requester already said yes
    to.
    """
    return "any" if request_row.lossy_accepted else request_row.desired_quality


def _query_for(request_row: Request) -> str:
    parts = [request_row.artist, request_row.title]
    return " ".join(p for p in parts if p)


async def _set_progress(
    session: AsyncSession,
    request_row: Request,
    *,
    percent: float | None,
    phase: str | None,
) -> None:
    """Record what the in-flight download is doing, for the request detail/list views to read.

    Committed immediately because the API serves these fields from a different session than
    the worker that observes them: an uncommitted value is invisible to the very poll it
    exists to answer.

    `percent` is None whenever no honest number is available, and that is passed straight
    through rather than coerced to 0.0. Only qBittorrent and slskd expose a real fraction; the
    ytdlp-shim tier has nothing but a job state, so it moves through here with a phase and a
    NULL percentage instead of a fabricated one.
    """
    request_row.download_progress_percent = percent
    request_row.download_phase = phase
    await session.commit()


async def _clear_progress(session: AsyncSession, request_row: Request) -> None:
    """Drop any in-flight progress, for a request that is no longer downloading.

    Called when a tier gives up on a candidate as well as when one succeeds: a stale "87%,
    receiving from peer" left on a request that has since moved on describes a transfer that
    is not running any more, which is a subtler version of the same lie as a fake bar.
    """
    await _set_progress(session, request_row, percent=None, phase=None)


async def _poll_until_settled(
    check: Callable[[], Awaitable[bool]],
    settings: Settings,
    *,
    on_tick: Callable[[], Awaitable[None]] | None = None,
) -> bool:
    """Poll `check` on a bounded interval until it returns True or the timeout elapses.

    `on_tick`, when given, runs after each unsuccessful check and is where a tier reports how
    far along its transfer is. It is deliberately the caller's own closure rather than a
    percentage this function computes: only the tier knows whether its backend can answer that
    question at all, and this loop must not invent a number for the one that cannot.

    A failing `on_tick` must never abort a download that is otherwise progressing, so it is
    called defensively: progress reporting is a display concern, and losing it is worth far
    less than the transfer it describes.

    Shared by the slskd and ytdlp-shim tiers, both of which start a real, non-instant transfer
    and then need to wait on it. `FULFILLMENT_POLL_INTERVAL_SECONDS` and
    `FULFILLMENT_DOWNLOAD_TIMEOUT_SECONDS` existed in Settings since Phase 1 but were never
    actually wired to anything: the slskd tier checked completion exactly once, immediately
    after enqueueing, which fails on essentially every real download since a Soulseek transfer
    is never done in the same instant it was started. This closes that gap for both tiers at
    once rather than fixing it only for the new one.

    This blocks the single fulfillment worker for up to the configured timeout, by design: the
    worker processes one job at a time and the timeout default (900s) is the intended per-job
    ceiling for a single-user, one-song-at-a-time service, not an incidental side effect.
    """
    deadline = (
        asyncio.get_event_loop().time() + settings.fulfillment_download_timeout_seconds
    )
    while asyncio.get_event_loop().time() < deadline:
        if await check():
            return True
        if on_tick is not None:
            try:
                await on_tick()
            except (
                Exception
            ) as exc:  # noqa: BLE001 - progress display never fails a download
                logger.warning("progress_report_failed", error=str(exc))
        await asyncio.sleep(settings.fulfillment_poll_interval_seconds)
    return False


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


async def _log_no_usable_candidate(
    session: AsyncSession,
    request_row: Request,
    *,
    backend: str,
    query_used: str,
    candidates_summary: list,
    lossy_only: bool,
    no_match_detail: str | None = None,
    lossy_only_detail: str = (
        "Found this track here, but only as lossy audio, and the request asked for lossless. "
        "Not downloading it without the requester's say-so."
    ),
) -> _TierResult:
    """Record why a tier that did get search results still has nothing it can download.

    Two genuinely different answers, kept apart in the audit trail because only one of them is
    worth going back to the requester about: either nothing here is the requested track at all
    (SEARCHED_NO_QUALIFYING_CANDIDATE, as before), or it is here but only in a lossy form the
    request asked Cadence not to accept (DECLINED_LOSSY_ONLY).
    """
    if lossy_only:
        await _log_attempt(
            session,
            request_row,
            backend=backend,
            query_used=query_used,
            candidates_considered=candidates_summary,
            chosen_candidate=None,
            outcome=FulfillmentOutcome.DECLINED_LOSSY_ONLY,
            detail=lossy_only_detail,
        )
        return _TierResult(fulfilled=False, lossy_only_matches=True)

    await _log_attempt(
        session,
        request_row,
        backend=backend,
        query_used=query_used,
        candidates_considered=candidates_summary,
        chosen_candidate=None,
        outcome=FulfillmentOutcome.SEARCHED_NO_QUALIFYING_CANDIDATE,
        detail=no_match_detail,
    )
    return _TierResult(fulfilled=False)


async def _finalize_file(
    session: AsyncSession,
    request_row: Request,
    settings: Settings,
    *,
    source_file: Path,
    preserve_source: bool = False,
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
    if preserve_source:
        # Copy via a hidden sibling then swap, so a partial or failed copy never leaves a
        # truncated file where the library (and Navidrome's scanner) expects a playable track.
        # os.replace is atomic here because the temp file is in dest's own directory.
        partial = dest.with_name(f".{dest.name}.partial")
        try:
            shutil.copy2(str(source_file), str(partial))
            os.replace(str(partial), str(dest))
        finally:
            partial.unlink(missing_ok=True)
    else:
        shutil.move(str(source_file), str(dest))

    tags = await collect_track_tags(
        title=request_row.title,
        artist=request_row.artist,
        album=request_row.album,
        musicbrainz_recording_id=request_row.musicbrainz_recording_id,
    )

    write_tags(dest, title=tags.title, artist=tags.artist, album=tags.album, tags=tags)

    request_row.status = RequestStatus.FULFILLED
    request_row.final_file_path = str(
        dest.relative_to(settings.music_library_path_resolved)
    )
    await session.commit()

    # Strictly after the commit above, never before or interleaved with it: a webhook
    # delivery failure must never affect the fulfillment that already succeeded.
    owner = await session.get(User, request_row.user_id)
    if owner is not None:
        await notify_webhook(owner, request_row, "fulfilled")


async def _try_prowlarr(
    session: AsyncSession, request_row: Request, settings: Settings, staging_dir: Path
) -> _TierResult:
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
        return _TierResult(fulfilled=False)

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
        return _TierResult(fulfilled=False)

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
        return _TierResult(fulfilled=False)

    scored = [
        ScoredCandidate(
            identifier=item.get("guid", ""),
            raw_name=item.get("title", ""),
            similarity=title_artist_similarity(
                item.get("title", ""), request_row.title, request_row.artist
            ),
            format_score=format_rank(extract_extension(item.get("title", ""))),
            matches_preference=matches_desired_quality(
                item.get("title", ""), request_row.desired_quality
            ),
            quality=candidate_quality(item.get("title", "")),
            size_bytes=item.get("size"),
            seeders=item.get("seeders"),
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

    require_lossless = _requires_lossless(request_row)
    ranked = qualifying_candidates_ranked(scored, require_lossless=require_lossless)
    if not ranked:
        return await _log_no_usable_candidate(
            session,
            request_row,
            backend="prowlarr",
            query_used=query,
            candidates_summary=candidates_summary,
            lossy_only=require_lossless and bool(qualifying_candidates_ranked(scored)),
            no_match_detail="No candidate met the minimum title/artist similarity threshold",
        )

    if not qbt.configured:
        await _log_attempt(
            session,
            request_row,
            backend="prowlarr",
            query_used=query,
            candidates_considered=candidates_summary,
            chosen_candidate=None,
            outcome=FulfillmentOutcome.SKIPPED_NOT_CONFIGURED,
            detail=(
                "Prowlarr is configured but qBittorrent is not, so a grab could never be "
                "confirmed"
            ),
        )
        return _TierResult(fulfilled=False)

    # Try candidates in score order, falling through to the next on any real failure.
    # Bounded: each attempt costs a real download-timeout wait.
    for candidate in ranked[: settings.fulfillment_max_candidate_attempts]:
        # Check for an already-present torrent BEFORE grabbing. A retry after a prior
        # DOWNLOAD_FAILED (the transfer never finished, not that grabbing itself failed)
        # re-scores the same candidates and would otherwise call grab() on a release
        # qBittorrent already has, which fails outright rather than resuming it. Adopting the
        # existing torrent instead is what the retry actually needs.
        torrent = await qbt.find_torrent_by_hint(candidate.raw_name)
        if torrent is not None:
            await _log_attempt(
                session,
                request_row,
                backend="prowlarr",
                query_used=query,
                candidates_considered=candidates_summary,
                chosen_candidate={
                    "name": candidate.raw_name,
                    "similarity": candidate.similarity,
                },
                outcome=FulfillmentOutcome.GRABBED,
                detail="Already present in qBittorrent from a prior attempt, resuming it",
            )
        else:
            indexer_id = candidate.extra.get("indexerId")
            grabbed = (
                await prowlarr.grab(candidate.identifier, indexer_id)
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
                    chosen_candidate={
                        "name": candidate.raw_name,
                        "similarity": candidate.similarity,
                    },
                    outcome=FulfillmentOutcome.ERROR,
                    detail="Grab call to Prowlarr failed",
                )
                continue

            await _log_attempt(
                session,
                request_row,
                backend="prowlarr",
                query_used=query,
                candidates_considered=candidates_summary,
                chosen_candidate={
                    "name": candidate.raw_name,
                    "similarity": candidate.similarity,
                },
                outcome=FulfillmentOutcome.GRABBED,
            )

            torrent = await qbt.find_torrent_by_hint(candidate.raw_name)
            if torrent is None:
                await _log_attempt(
                    session,
                    request_row,
                    backend="prowlarr",
                    query_used=query,
                    candidates_considered=None,
                    chosen_candidate={
                        "name": candidate.raw_name,
                        "similarity": candidate.similarity,
                    },
                    outcome=FulfillmentOutcome.ERROR,
                    detail="Grabbed via Prowlarr but qBittorrent never picked it up",
                )
                continue

        torrent_hash = torrent["hash"]

        async def _check(torrent_hash: str = torrent_hash) -> bool:
            return await qbt.is_complete(torrent_hash)

        # The one tier with a genuine fraction to report: qBittorrent's `progress` is a 0..1
        # float on the same payload the completion check already reads.
        async def _report(torrent_hash: str = torrent_hash) -> None:
            percent = await qbt.progress_percent(torrent_hash)
            await _set_progress(
                session,
                request_row,
                percent=percent,
                phase="Downloading from the torrent swarm",
            )

        await _set_progress(
            session,
            request_row,
            percent=None,
            phase="Waiting for the torrent to start",
        )
        completed = await _poll_until_settled(_check, settings, on_tick=_report)
        if not completed:
            await _clear_progress(session, request_row)
        if not completed:
            await _log_attempt(
                session,
                request_row,
                backend="prowlarr",
                query_used=query,
                candidates_considered=None,
                chosen_candidate={
                    "name": candidate.raw_name,
                    "similarity": candidate.similarity,
                },
                outcome=FulfillmentOutcome.DOWNLOAD_FAILED,
                detail=(
                    f"Torrent did not complete within "
                    f"{settings.fulfillment_download_timeout_seconds}s"
                ),
            )
            continue

        content_name = await qbt.get_content_path(torrent["hash"])
        if not content_name:
            await _log_attempt(
                session,
                request_row,
                backend="prowlarr",
                query_used=query,
                candidates_considered=None,
                chosen_candidate={
                    "name": candidate.raw_name,
                    "similarity": candidate.similarity,
                },
                outcome=FulfillmentOutcome.DOWNLOAD_FAILED,
                detail="Torrent completed but qBittorrent reported no content path",
            )
            continue

        save_path = Path(torrent.get("save_path", staging_dir))
        source_file = save_path / content_name
        if not source_file.exists():
            await _log_attempt(
                session,
                request_row,
                backend="prowlarr",
                query_used=query,
                candidates_considered=None,
                chosen_candidate={
                    "name": candidate.raw_name,
                    "similarity": candidate.similarity,
                },
                outcome=FulfillmentOutcome.DOWNLOAD_FAILED,
                detail=f"Expected completed file not found at {source_file}",
            )
            continue

        await _log_attempt(
            session,
            request_row,
            backend="prowlarr",
            query_used=query,
            candidates_considered=None,
            chosen_candidate=None,
            outcome=FulfillmentOutcome.DOWNLOAD_COMPLETED,
        )
        # preserve_source: this file is qBittorrent's, and it is still seeding it.
        await _finalize_file(
            session,
            request_row,
            settings,
            source_file=source_file,
            preserve_source=True,
        )
        return _TierResult(fulfilled=True)

    return _TierResult(fulfilled=False)


async def _try_slskd(
    session: AsyncSession, request_row: Request, settings: Settings, staging_dir: Path
) -> _TierResult:
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
        return _TierResult(fulfilled=False)

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
        return _TierResult(fulfilled=False)

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
        return _TierResult(fulfilled=False)

    scored = [
        ScoredCandidate(
            identifier=f.get("filename", ""),
            raw_name=f.get("filename", ""),
            similarity=title_artist_similarity(
                f.get("filename", ""), request_row.title, request_row.artist
            ),
            format_score=format_rank(extract_extension(f.get("filename", ""))),
            matches_preference=matches_desired_quality(
                f.get("filename", ""), request_row.desired_quality
            ),
            quality=candidate_quality(f.get("filename", "")),
            size_bytes=f.get("size"),
            extra={"username": f.get("username"), "bitRate": f.get("bitRate")},
        )
        for f in files
    ]
    candidates_summary = [
        {"name": c.raw_name, "similarity": round(c.similarity, 3), "extra": c.extra}
        for c in scored
    ]

    require_lossless = _requires_lossless(request_row)
    ranked = qualifying_candidates_ranked(scored, require_lossless=require_lossless)
    if not ranked:
        return await _log_no_usable_candidate(
            session,
            request_row,
            backend="slskd",
            query_used=query,
            candidates_summary=candidates_summary,
            lossy_only=require_lossless and bool(qualifying_candidates_ranked(scored)),
        )

    # Try candidates in score order, falling through to the next on any real failure (a peer
    # gone offline mid-transfer, a transfer that never completes). Bounded: each attempt costs
    # a real download-timeout wait.
    for candidate in ranked[: settings.fulfillment_max_candidate_attempts]:
        username = candidate.extra.get("username")
        if not username:
            await _log_attempt(
                session,
                request_row,
                backend="slskd",
                query_used=query,
                candidates_considered=candidates_summary,
                chosen_candidate={
                    "name": candidate.raw_name,
                    "similarity": candidate.similarity,
                },
                outcome=FulfillmentOutcome.ERROR,
                detail="Selected Soulseek candidate had no associated username",
            )
            continue
        started = await slskd.enqueue_download(
            username, candidate.identifier, candidate.size_bytes
        )
        if not started:
            await _log_attempt(
                session,
                request_row,
                backend="slskd",
                query_used=query,
                candidates_considered=candidates_summary,
                chosen_candidate={
                    "name": candidate.raw_name,
                    "similarity": candidate.similarity,
                },
                outcome=FulfillmentOutcome.ERROR,
                detail="Failed to enqueue Soulseek download",
            )
            continue

        await _log_attempt(
            session,
            request_row,
            backend="slskd",
            query_used=query,
            candidates_considered=candidates_summary,
            chosen_candidate={
                "name": candidate.raw_name,
                "similarity": candidate.similarity,
            },
            outcome=FulfillmentOutcome.DOWNLOAD_STARTED,
        )

        # Same narrowing gap as job_id elsewhere in this file: the `if not username: continue`
        # guard above already rules out None, but that does not reach a nested function's own
        # default value, hence the explicit re-typed local.
        confirmed_username: str = username
        candidate_identifier = candidate.identifier

        async def _check(
            username: str = confirmed_username, identifier: str = candidate_identifier
        ) -> bool:
            return await slskd.is_download_complete(username, identifier)

        # Verified against a live slskd rather than assumed: the per-file transfer object this
        # tier already polls for completion also carries `percentComplete` and the byte counts
        # behind it, so this is a real measurement, not an estimate.
        async def _report(
            username: str = confirmed_username, identifier: str = candidate_identifier
        ) -> None:
            percent = await slskd.download_progress(username, identifier)
            await _set_progress(
                session,
                request_row,
                percent=percent,
                phase="Receiving from a Soulseek peer",
            )

        await _set_progress(
            session,
            request_row,
            percent=None,
            phase="Waiting for a Soulseek peer",
        )
        completed = await _poll_until_settled(_check, settings, on_tick=_report)
        if not completed:
            await _clear_progress(session, request_row)
        if not completed:
            await _log_attempt(
                session,
                request_row,
                backend="slskd",
                query_used=query,
                candidates_considered=None,
                chosen_candidate={
                    "name": candidate.raw_name,
                    "similarity": candidate.similarity,
                },
                outcome=FulfillmentOutcome.DOWNLOAD_FAILED,
                detail=(
                    f"Transfer did not complete within "
                    f"{settings.fulfillment_download_timeout_seconds}s"
                ),
            )
            continue

        source_file = staging_dir / Path(candidate.identifier).name
        if not source_file.exists():
            await _log_attempt(
                session,
                request_row,
                backend="slskd",
                query_used=query,
                candidates_considered=None,
                chosen_candidate={
                    "name": candidate.raw_name,
                    "similarity": candidate.similarity,
                },
                outcome=FulfillmentOutcome.DOWNLOAD_FAILED,
                detail=f"Expected completed file not found at {source_file}",
            )
            continue

        await _log_attempt(
            session,
            request_row,
            backend="slskd",
            query_used=query,
            candidates_considered=None,
            chosen_candidate=None,
            outcome=FulfillmentOutcome.DOWNLOAD_COMPLETED,
        )
        # Move, not copy: source is Cadence's own staging dir, nothing is seeding it.
        await _finalize_file(
            session,
            request_row,
            settings,
            source_file=source_file,
            preserve_source=False,
        )
        return _TierResult(fulfilled=True)

    return _TierResult(fulfilled=False)


async def _try_ytdlp(
    session: AsyncSession, request_row: Request, settings: Settings, staging_dir: Path
) -> _TierResult:
    ytdlp = YtdlpShimClient(settings.ytdlp_shim_url)
    query = _query_for(request_row)

    if not ytdlp.configured:
        await _log_attempt(
            session,
            request_row,
            backend="ytdlp",
            query_used=query,
            candidates_considered=None,
            chosen_candidate=None,
            outcome=FulfillmentOutcome.SKIPPED_NOT_CONFIGURED,
        )
        return _TierResult(fulfilled=False)

    try:
        results = await ytdlp.search(query)
    except Exception as exc:  # noqa: BLE001
        await _log_attempt(
            session,
            request_row,
            backend="ytdlp",
            query_used=query,
            candidates_considered=None,
            chosen_candidate=None,
            outcome=FulfillmentOutcome.ERROR,
            detail=str(exc),
        )
        return _TierResult(fulfilled=False)

    if not results:
        await _log_attempt(
            session,
            request_row,
            backend="ytdlp",
            query_used=query,
            candidates_considered=[],
            chosen_candidate=None,
            outcome=FulfillmentOutcome.SEARCHED_NO_CANDIDATES,
        )
        return _TierResult(fulfilled=False)

    # A YouTube video title is the only identity signal available before download (there is no
    # file extension or bitrate to weigh the way Prowlarr/slskd candidates carry), so
    # format_score stays at its neutral default. Quality, though, is not unknown here: YouTube
    # serves Opus/AAC and nothing else, so every candidate from this tier is flatly lossy, and
    # saying so outright is what lets a lossless request be declined honestly instead of
    # "succeeding" with a transcode of a lossy source. seeders is left unset (None), which
    # ScoredCandidate treats as "not applicable" rather than "zero", the same way a Soulseek
    # candidate has no torrent swarm to weigh either.
    scored = [
        ScoredCandidate(
            identifier=r.get("video_id", ""),
            raw_name=r.get("title", ""),
            similarity=title_artist_similarity(
                r.get("title", ""), request_row.title, request_row.artist
            ),
            format_score=0,
            matches_preference=request_row.desired_quality in ("any", QUALITY_LOSSY),
            quality=QUALITY_LOSSY,
            size_bytes=None,
            extra={
                "uploader": r.get("uploader"),
                "duration_seconds": r.get("duration_seconds"),
            },
        )
        for r in results
    ]
    candidates_summary = [
        {"name": c.raw_name, "similarity": round(c.similarity, 3), "extra": c.extra}
        for c in scored
    ]

    require_lossless = _requires_lossless(request_row)
    ranked = qualifying_candidates_ranked(scored, require_lossless=require_lossless)
    if not ranked:
        # The search still runs before this check, cheap and deliberate: knowing whether
        # YouTube even has the track is what makes the question put to the requester an honest
        # one ("we found it, but only lossy") instead of a guess.
        return await _log_no_usable_candidate(
            session,
            request_row,
            backend="ytdlp",
            query_used=query,
            candidates_summary=candidates_summary,
            lossy_only=require_lossless and bool(qualifying_candidates_ranked(scored)),
            lossy_only_detail=(
                "Found this track on YouTube, but YouTube has no lossless source at all: "
                "every stream here is Opus or AAC. This tier cannot satisfy a lossless "
                "request, so it declined rather than handing over a lossy file."
            ),
        )

    # Try candidates in score order, falling through to the next on any real failure. A
    # candidate can score well and still not pan out for a reason scoring cannot see: this
    # tier once picked a video removed for a copyright claim between being indexed and being
    # fetched, and simply gave up rather than trying the next of nine other qualifying results
    # it had already found. Bounded, since each attempt costs a real download-timeout wait.
    for candidate in ranked[: settings.fulfillment_max_candidate_attempts]:
        # The shim is told what this request wants rather than being left to assume. Belt and
        # braces on top of the check above, not a substitute for it: the shim refuses a
        # lossless job outright, so even a future regression in this tier's own filtering
        # cannot make it quietly hand back an Opus file for a lossless request again.
        job_id = await ytdlp.start_download(
            candidate.identifier, quality=_quality_ask_for_shim(request_row)
        )
        if not job_id:
            await _log_attempt(
                session,
                request_row,
                backend="ytdlp",
                query_used=query,
                candidates_considered=candidates_summary,
                chosen_candidate={
                    "name": candidate.raw_name,
                    "similarity": candidate.similarity,
                },
                outcome=FulfillmentOutcome.ERROR,
                detail="Failed to start ytdlp-shim download job",
            )
            continue

        await _log_attempt(
            session,
            request_row,
            backend="ytdlp",
            query_used=query,
            candidates_considered=candidates_summary,
            chosen_candidate={
                "name": candidate.raw_name,
                "similarity": candidate.similarity,
            },
            outcome=FulfillmentOutcome.DOWNLOAD_STARTED,
        )

        # job_id is `str` from here on (the `if not job_id: continue` guard above already
        # ruled out None), but that narrowing does not reach a nested function's own default
        # value, hence the explicit re-typed local rather than reusing job_id directly there.
        started_job_id: str = job_id

        # _poll_until_settled only reports whether the check finally returned True; the
        # actual state/filename/error the shim last reported is captured here as a side
        # effect of the closure, since that detail (not just a bool) is what the audit trail
        # needs.
        last_status: list[tuple[str, str | None, str | None]] = []

        async def _check(
            job_id: str = started_job_id, last_status: list = last_status
        ) -> bool:
            status = await ytdlp.get_download_status(job_id)
            last_status.append(status)
            return status[0] not in ("running", "unknown")

        # No percentage here, ever. The shim reports a job state and nothing else (see
        # ytdlp-shim/app/main.py: yt-dlp is run to completion as a subprocess, with no byte
        # counts surfaced), so this tier says what it is doing and leaves the number NULL
        # rather than showing a bar that would only be tracking the clock.
        async def _report(last_status: list = last_status) -> None:
            state = last_status[-1][0] if last_status else "unknown"
            phase = (
                "Fetching the audio from YouTube"
                if state == "running"
                else "Waiting for YouTube"
            )
            await _set_progress(session, request_row, percent=None, phase=phase)

        await _set_progress(
            session,
            request_row,
            percent=None,
            phase="Fetching the audio from YouTube",
        )
        settled = await _poll_until_settled(_check, settings, on_tick=_report)
        state, filename, error = (
            last_status[-1] if last_status else ("unknown", None, None)
        )
        if not settled:
            state, error = "timed_out", (
                f"Cadence gave up waiting for the ytdlp-shim job after "
                f"{settings.fulfillment_download_timeout_seconds}s"
            )
        if state != "completed" or not filename:
            await _log_attempt(
                session,
                request_row,
                backend="ytdlp",
                query_used=query,
                candidates_considered=None,
                chosen_candidate={
                    "name": candidate.raw_name,
                    "similarity": candidate.similarity,
                },
                outcome=FulfillmentOutcome.DOWNLOAD_FAILED,
                detail=error or f"ytdlp-shim job ended in state {state!r}",
            )
            await _clear_progress(session, request_row)
            continue

        source_file = staging_dir / filename
        if not source_file.exists():
            await _log_attempt(
                session,
                request_row,
                backend="ytdlp",
                query_used=query,
                candidates_considered=None,
                chosen_candidate={
                    "name": candidate.raw_name,
                    "similarity": candidate.similarity,
                },
                outcome=FulfillmentOutcome.DOWNLOAD_FAILED,
                detail=f"Expected completed file not found at {source_file}",
            )
            continue

        await _log_attempt(
            session,
            request_row,
            backend="ytdlp",
            query_used=query,
            candidates_considered=None,
            chosen_candidate=None,
            outcome=FulfillmentOutcome.DOWNLOAD_COMPLETED,
        )
        # Move, not copy: same as slskd, this is Cadence's own staging dir and nothing else
        # needs the file once it is filed into the library.
        await _finalize_file(
            session,
            request_row,
            settings,
            source_file=source_file,
            preserve_source=False,
        )
        return _TierResult(fulfilled=True)

    return _TierResult(fulfilled=False)


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

    # Resolve DB-saved admin settings (env-var-wins-if-set, per settings_store's precedence
    # rule) fresh for every fulfillment pass, so a save made through the settings UI after a
    # request was approved is honored on this attempt without a worker/container restart.
    settings = await apply_effective_settings(session, settings)

    # Before anything is searched or grabbed: a full library means this download cannot be
    # filed no matter which tier finds it, and finding out at filing time wastes the download
    # and can leave a truncated file where the library scanner will see it.
    no_room = insufficient_space_reason(
        settings.music_library_path_resolved, settings.min_free_disk_gb
    )
    if no_room:
        logger.warning(
            "fulfillment_refused_no_disk_space",
            request_id=request_row.id,
            reason=no_room,
        )
        request_row.status = RequestStatus.FAILED
        request_row.failure_reason = no_room
        await session.commit()
        return

    request_row.status = RequestStatus.DOWNLOADING
    await session.commit()

    staging_dir = settings.data_dir_path / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    lossy_only_matches = False
    try:
        for tier in (_try_prowlarr, _try_slskd, _try_ytdlp):
            result = await tier(session, request_row, settings, staging_dir)
            if result.fulfilled:
                return
            lossy_only_matches = lossy_only_matches or result.lossy_only_matches
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

    if lossy_only_matches:
        # The honest end state for "we found your track, just never in lossless". Not FAILED
        # (nothing failed, and a retry would find the same lossy copies again) and emphatically
        # not FULFILLED with one of those copies, which is the exact silent downgrade this
        # whole flow exists to stop. It waits here until the requester answers.
        request_row.status = RequestStatus.AWAITING_QUALITY
        request_row.failure_reason = None
        await session.commit()

        # Strictly after the commit, and for the same reason approve/fulfil fire one: a
        # request parked on a question the requester has not seen yet is the one state where
        # telling them actually changes the outcome.
        owner = await session.get(User, request_row.user_id)
        if owner is not None:
            await notify_webhook(owner, request_row, "awaiting_quality")
        return

    request_row.status = RequestStatus.FAILED
    request_row.failure_reason = (
        "No qualifying release found on configured indexers, and neither the Soulseek nor "
        "the YouTube fallback found an exact-enough match (or either is not configured) "
        "within the attempt budget."
    )
    await session.commit()
