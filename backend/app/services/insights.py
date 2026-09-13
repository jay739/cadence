"""Read-only aggregations over the fulfillment audit trail, for the admin insight page.

Nothing here records anything new. Every fulfillment pass already writes one FulfillmentAttempt
row per decision it makes (see app.models.request.FulfillmentAttempt and the tiers in
app.services.fulfillment), so the question "is this indexer earning its keep?" is already
answerable from data on disk -- it had simply never been asked.

Scale assumption: Cadence is a self-hosted, one-track-per-row service, so both `requests` and
`fulfillment_attempts` stay in the thousands-of-rows range for a realistic install. These
queries therefore stay deliberately plain (a handful of GROUP BYs, no window functions, no
materialized rollups) and lean on the two indexes that already exist for other reasons:
`fulfillment_attempts.created_at` and `fulfillment_attempts.request_id`.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import TypedDict

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.request import (
    FulfillmentAttempt,
    FulfillmentOutcome,
    Request,
    RequestStatus,
)

# The order the fulfillment worker actually tries them in (app.services.fulfillment.run_pass),
# not alphabetical: reading the scoreboard top to bottom should mirror the path a request takes.
# Any backend found in the data but missing from this list is appended afterwards, so a tier
# added later still shows up without needing this constant updated first.
TIER_ORDER: tuple[str, ...] = ("prowlarr", "slskd", "ytdlp")

# The one outcome that means the tier never ran at all. It must not count against a tier's
# success rate: an unconfigured Prowlarr did not fail to find anything, it was never asked.
NON_RUN_OUTCOMES: frozenset[str] = frozenset(
    {FulfillmentOutcome.SKIPPED_NOT_CONFIGURED.value}
)

# The single outcome that means a file actually landed. GRABBED and DOWNLOAD_STARTED are
# mid-flight bookkeeping, and a successful Prowlarr pass writes GRABBED *and* then
# DOWNLOAD_COMPLETED -- counting either as a success would score one delivery twice.
DELIVERED_OUTCOME: str = FulfillmentOutcome.DOWNLOAD_COMPLETED.value

# Outcomes that end a tier's pass with no file. DECLINED_LOSSY_ONLY is in here because it is a
# reason a request did not complete, but it is a deliberate refusal rather than a fault -- the
# UI labels it as such rather than lumping it in with real breakage.
FAILURE_OUTCOMES: frozenset[str] = frozenset(
    {
        FulfillmentOutcome.SEARCHED_NO_CANDIDATES.value,
        FulfillmentOutcome.SEARCHED_NO_QUALIFYING_CANDIDATE.value,
        FulfillmentOutcome.DECLINED_LOSSY_ONLY.value,
        FulfillmentOutcome.DOWNLOAD_FAILED.value,
        FulfillmentOutcome.ERROR.value,
    }
)

TOP_ARTIST_LIMIT = 8


class TierStats(TypedDict):
    backend: str
    attempts: int
    requests_tried: int
    requests_delivered: int
    success_rate: float | None
    outcomes: dict[str, int]


class FailureCount(TypedDict):
    outcome: str
    count: int
    backends: dict[str, int]


class DailyVolume(TypedDict):
    date: str
    requests: int


class TopArtist(TypedDict):
    artist: str
    requests: int


class InsightsData(TypedDict):
    window_days: int
    since: str
    total_requests: int
    status_counts: dict[str, int]
    tiers: list[TierStats]
    failures: list[FailureCount]
    daily_volume: list[DailyVolume]
    top_artists: list[TopArtist]


def _window_start(days: int, *, today: date) -> datetime:
    """Midnight UTC of the first day in the window, inclusive of today.

    Anchored to a date boundary rather than "now minus N*24h" so the daily volume series has
    exactly `days` buckets, the last of which is today -- a rolling-hours cutoff would leave a
    half-populated bucket at each end and make the first bar always look like a dip.
    """
    start_date = today - timedelta(days=days - 1)
    return datetime(start_date.year, start_date.month, start_date.day, tzinfo=UTC)


async def _tier_stats(session: AsyncSession, since: datetime) -> list[TierStats]:
    """Per-tier attempt counts, outcome breakdown, and delivery rate.

    Two queries rather than one: the outcome breakdown needs rows grouped by (backend, outcome),
    but "how many distinct requests did this tier actually run against" cannot be summed from
    those groups -- one request legitimately appears under several outcomes in the same pass
    (GRABBED then DOWNLOAD_COMPLETED), so adding the per-outcome distinct counts would
    double-count it. The second query asks the database for that distinct count directly.
    """
    grouped = await session.execute(
        select(
            FulfillmentAttempt.backend,
            FulfillmentAttempt.outcome,
            func.count().label("attempts"),
            func.count(distinct(FulfillmentAttempt.request_id)).label("requests"),
        )
        .where(FulfillmentAttempt.created_at >= since)
        .group_by(FulfillmentAttempt.backend, FulfillmentAttempt.outcome)
    )

    attempts_by_backend: dict[str, int] = defaultdict(int)
    outcomes_by_backend: dict[str, dict[str, int]] = defaultdict(dict)
    delivered_by_backend: dict[str, int] = defaultdict(int)

    for backend, outcome, attempts, requests in grouped.all():
        attempts_by_backend[backend] += attempts
        outcomes_by_backend[backend][outcome] = attempts
        if outcome == DELIVERED_OUTCOME:
            delivered_by_backend[backend] = requests

    tried = await session.execute(
        select(
            FulfillmentAttempt.backend,
            func.count(distinct(FulfillmentAttempt.request_id)),
        )
        .where(
            FulfillmentAttempt.created_at >= since,
            FulfillmentAttempt.outcome.notin_(NON_RUN_OUTCOMES),
        )
        .group_by(FulfillmentAttempt.backend)
    )
    tried_by_backend: dict[str, int] = dict(tried.all())  # type: ignore[arg-type]

    # Every known tier is reported even with no rows at all: "this tier was never asked" is
    # itself an answer to whether it is earning its keep, and silently omitting it would read
    # as though it simply had nothing to report.
    seen = set(attempts_by_backend) | set(tried_by_backend)
    ordered = list(TIER_ORDER) + sorted(seen - set(TIER_ORDER))

    stats: list[TierStats] = []
    for backend in ordered:
        requests_tried = tried_by_backend.get(backend, 0)
        requests_delivered = delivered_by_backend.get(backend, 0)
        stats.append(
            TierStats(
                backend=backend,
                attempts=attempts_by_backend.get(backend, 0),
                requests_tried=requests_tried,
                requests_delivered=requests_delivered,
                # None, not 0.0: a tier that never ran has no rate, and showing it as 0% would
                # accuse an unconfigured indexer of failing every request it was handed.
                success_rate=(
                    requests_delivered / requests_tried if requests_tried else None
                ),
                outcomes=dict(sorted(outcomes_by_backend.get(backend, {}).items())),
            )
        )
    return stats


async def _failure_counts(session: AsyncSession, since: datetime) -> list[FailureCount]:
    """Which no-file outcomes happen most, and which tiers produce them."""
    rows = await session.execute(
        select(
            FulfillmentAttempt.outcome,
            FulfillmentAttempt.backend,
            func.count().label("count"),
        )
        .where(
            FulfillmentAttempt.created_at >= since,
            FulfillmentAttempt.outcome.in_(FAILURE_OUTCOMES),
        )
        .group_by(FulfillmentAttempt.outcome, FulfillmentAttempt.backend)
    )

    totals: dict[str, int] = defaultdict(int)
    by_backend: dict[str, dict[str, int]] = defaultdict(dict)
    for outcome, backend, count in rows.all():
        totals[outcome] += count
        by_backend[outcome][backend] = count

    # Tie-broken on the outcome name so the list is stable between calls on identical data.
    return [
        FailureCount(
            outcome=outcome,
            count=count,
            backends=dict(sorted(by_backend[outcome].items())),
        )
        for outcome, count in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


async def _daily_volume(
    session: AsyncSession, since: datetime, *, days: int, today: date
) -> list[DailyVolume]:
    """Requests created per day, with empty days filled in as zero.

    Grouping happens in SQL via SQLite's strftime rather than by pulling every row back and
    bucketing in Python. That ties this one query to SQLite, which Cadence already commits to
    everywhere else (aiosqlite is the only driver in the dependency list, and
    app.db.base.init_engine is only ever handed a sqlite+aiosqlite URL).

    Zero-fill happens here rather than in the client so the series is always exactly `days`
    long: a chart that silently drops quiet days compresses the timeline and makes a gap look
    like steady activity.
    """
    day_expr = func.strftime("%Y-%m-%d", Request.created_at)
    rows = await session.execute(
        select(day_expr, func.count())
        .where(Request.created_at >= since)
        .group_by(day_expr)
    )
    counts: dict[str, int] = dict(rows.all())  # type: ignore[arg-type]

    series: list[DailyVolume] = []
    for offset in range(days):
        day = (today - timedelta(days=days - 1 - offset)).isoformat()
        series.append(DailyVolume(date=day, requests=counts.get(day, 0)))
    return series


async def _top_artists(session: AsyncSession, since: datetime) -> list[TopArtist]:
    """The most-requested artists in the window.

    Artist rather than exact track on purpose: requests are deduplicated per user+track, so on
    a small install a per-track ranking is a wall of ones that says nothing, while the artist
    ranking actually tells an operator what their listeners are into.
    """
    rows = await session.execute(
        select(Request.artist, func.count().label("count"))
        .where(Request.created_at >= since)
        .group_by(Request.artist)
        .order_by(func.count().desc(), Request.artist.asc())
        .limit(TOP_ARTIST_LIMIT)
    )
    return [TopArtist(artist=artist, requests=count) for artist, count in rows.all()]


async def _status_counts(session: AsyncSession, since: datetime) -> dict[str, int]:
    """Per-status counts of every request created in the window, across all users.

    Deliberately a GROUP BY, unlike the per-user equivalent in app.services.requests, which
    counts in Python because one requester's history is tiny. This one is server-wide.
    """
    rows = await session.execute(
        select(Request.status, func.count())
        .where(Request.created_at >= since)
        .group_by(Request.status)
    )
    counts = {status.value: 0 for status in RequestStatus}
    for status, count in rows.all():
        counts[status.value] = count
    return counts


async def get_fulfillment_insights(session: AsyncSession, *, days: int) -> InsightsData:
    """Everything the admin insight page renders, in one round trip.

    One endpoint rather than five: the page shows a single window of time, and splitting it
    would let the sections disagree with each other if a request landed mid-load.
    """
    today = datetime.now(UTC).date()
    since = _window_start(days, today=today)
    status_counts = await _status_counts(session, since)

    return InsightsData(
        window_days=days,
        since=since.date().isoformat(),
        total_requests=sum(status_counts.values()),
        status_counts=status_counts,
        tiers=await _tier_stats(session, since),
        failures=await _failure_counts(session, since),
        daily_volume=await _daily_volume(session, since, days=days, today=today),
        top_artists=await _top_artists(session, since),
    )
