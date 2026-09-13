"""Aggregation math for the admin insight page, against a hand-built set of attempt rows.

The numbers here are asserted exactly rather than loosely (">= 1 attempt") on purpose: the
whole value of this page is that an operator can decide whether to keep paying for an indexer
based on it, so a rate that is quietly wrong is worse than no page at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.request import (
    FulfillmentAttempt,
    FulfillmentOutcome,
    Request,
    RequestStatus,
)
from app.models.user import User
from app.services.insights import get_fulfillment_insights
from app.services.requests import compute_dedupe_key

pytestmark = pytest.mark.asyncio


async def _make_user(session, email: str = "insights@example.com") -> User:
    user = User(email=email, password_hash="not-a-real-hash")
    session.add(user)
    await session.flush()
    return user


async def _make_request(
    session,
    user: User,
    *,
    title: str = "Naatu Naatu",
    artist: str = "Rahul Sipligunj",
    source_id: str = "t-1",
    status: RequestStatus = RequestStatus.PENDING,
    days_ago: int = 0,
) -> Request:
    created = datetime.now(UTC) - timedelta(days=days_ago)
    row = Request(
        user_id=user.id,
        title=title,
        artist=artist,
        album=None,
        source="apple",
        source_id=source_id,
        dedupe_key=compute_dedupe_key("apple", source_id),
        status=status,
        created_at=created,
        updated_at=created,
    )
    session.add(row)
    await session.flush()
    return row


async def _attempt(
    session,
    request_row: Request,
    *,
    backend: str,
    outcome: FulfillmentOutcome,
    days_ago: int = 0,
) -> None:
    session.add(
        FulfillmentAttempt(
            request_id=request_row.id,
            backend=backend,
            query_used="q",
            outcome=outcome.value,
            created_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
    )
    await session.flush()


def _tier(data, backend: str):
    return next(t for t in data["tiers"] if t["backend"] == backend)


class TestTierSuccessRate:
    async def test_a_successful_pass_counts_once_despite_writing_two_rows(
        self, db_session
    ):
        """A completed Prowlarr pass writes GRABBED *and* DOWNLOAD_COMPLETED.

        Counting rows would score one delivery twice and report a 200% success rate; the rate
        is therefore measured over distinct requests, not over audit rows.
        """
        user = await _make_user(db_session)
        req = await _make_request(db_session, user, source_id="grabbed-then-done")
        await _attempt(
            db_session, req, backend="prowlarr", outcome=FulfillmentOutcome.GRABBED
        )
        await _attempt(
            db_session,
            req,
            backend="prowlarr",
            outcome=FulfillmentOutcome.DOWNLOAD_COMPLETED,
        )

        data = await get_fulfillment_insights(db_session, days=30)
        prowlarr = _tier(data, "prowlarr")

        assert prowlarr["attempts"] == 2
        assert prowlarr["requests_tried"] == 1
        assert prowlarr["requests_delivered"] == 1
        assert prowlarr["success_rate"] == 1.0

    async def test_rate_is_delivered_over_tried(self, db_session):
        user = await _make_user(db_session)
        for index in range(4):
            req = await _make_request(db_session, user, source_id=f"slskd-{index}")
            # One of the four completes; the other three search and find nothing qualifying.
            outcome = (
                FulfillmentOutcome.DOWNLOAD_COMPLETED
                if index == 0
                else FulfillmentOutcome.SEARCHED_NO_QUALIFYING_CANDIDATE
            )
            await _attempt(db_session, req, backend="slskd", outcome=outcome)

        data = await get_fulfillment_insights(db_session, days=30)
        slskd = _tier(data, "slskd")

        assert slskd["requests_tried"] == 4
        assert slskd["requests_delivered"] == 1
        assert slskd["success_rate"] == 0.25

    async def test_skipped_passes_do_not_count_against_a_tier(self, db_session):
        """An unconfigured tier did not fail its requests, it was never asked."""
        user = await _make_user(db_session)
        skipped = await _make_request(db_session, user, source_id="skipped")
        await _attempt(
            db_session,
            skipped,
            backend="ytdlp",
            outcome=FulfillmentOutcome.SKIPPED_NOT_CONFIGURED,
        )
        ran = await _make_request(db_session, user, source_id="ran")
        await _attempt(
            db_session,
            ran,
            backend="ytdlp",
            outcome=FulfillmentOutcome.DOWNLOAD_COMPLETED,
        )

        data = await get_fulfillment_insights(db_session, days=30)
        ytdlp = _tier(data, "ytdlp")

        assert ytdlp["attempts"] == 2
        assert ytdlp["requests_tried"] == 1
        assert ytdlp["success_rate"] == 1.0

    async def test_a_tier_that_never_ran_has_no_rate_rather_than_zero(self, db_session):
        user = await _make_user(db_session)
        req = await _make_request(db_session, user, source_id="only-prowlarr")
        await _attempt(
            db_session,
            req,
            backend="prowlarr",
            outcome=FulfillmentOutcome.DOWNLOAD_COMPLETED,
        )

        data = await get_fulfillment_insights(db_session, days=30)

        assert _tier(data, "slskd")["success_rate"] is None
        assert _tier(data, "slskd")["requests_tried"] == 0

    async def test_every_known_tier_is_reported_even_with_no_data_at_all(
        self, db_session
    ):
        data = await get_fulfillment_insights(db_session, days=30)
        assert [t["backend"] for t in data["tiers"]] == ["prowlarr", "slskd", "ytdlp"]
        assert all(t["success_rate"] is None for t in data["tiers"])

    async def test_an_unknown_backend_is_reported_after_the_known_tiers(
        self, db_session
    ):
        user = await _make_user(db_session)
        req = await _make_request(db_session, user, source_id="future-tier")
        await _attempt(
            db_session, req, backend="zzz-new-tier", outcome=FulfillmentOutcome.GRABBED
        )

        data = await get_fulfillment_insights(db_session, days=30)
        assert [t["backend"] for t in data["tiers"]] == [
            "prowlarr",
            "slskd",
            "ytdlp",
            "zzz-new-tier",
        ]

    async def test_outcome_breakdown_counts_every_row(self, db_session):
        user = await _make_user(db_session)
        req = await _make_request(db_session, user, source_id="breakdown")
        await _attempt(
            db_session, req, backend="prowlarr", outcome=FulfillmentOutcome.ERROR
        )
        await _attempt(
            db_session, req, backend="prowlarr", outcome=FulfillmentOutcome.ERROR
        )
        await _attempt(
            db_session, req, backend="prowlarr", outcome=FulfillmentOutcome.GRABBED
        )

        data = await get_fulfillment_insights(db_session, days=30)
        assert _tier(data, "prowlarr")["outcomes"] == {"error": 2, "grabbed": 1}


class TestFailureCounts:
    async def test_ranked_by_frequency_with_the_producing_tiers_named(self, db_session):
        user = await _make_user(db_session)
        for index in range(3):
            req = await _make_request(db_session, user, source_id=f"nc-{index}")
            await _attempt(
                db_session,
                req,
                backend="prowlarr",
                outcome=FulfillmentOutcome.SEARCHED_NO_CANDIDATES,
            )
        req = await _make_request(db_session, user, source_id="err")
        await _attempt(
            db_session, req, backend="slskd", outcome=FulfillmentOutcome.ERROR
        )
        await _attempt(
            db_session, req, backend="prowlarr", outcome=FulfillmentOutcome.ERROR
        )

        data = await get_fulfillment_insights(db_session, days=30)

        assert data["failures"][0] == {
            "outcome": "searched_no_candidates",
            "count": 3,
            "backends": {"prowlarr": 3},
        }
        assert data["failures"][1] == {
            "outcome": "error",
            "count": 2,
            "backends": {"prowlarr": 1, "slskd": 1},
        }

    async def test_mid_flight_outcomes_are_not_reported_as_failures(self, db_session):
        """GRABBED and DOWNLOAD_STARTED are bookkeeping, not something that went wrong."""
        user = await _make_user(db_session)
        req = await _make_request(db_session, user, source_id="midflight")
        await _attempt(
            db_session, req, backend="prowlarr", outcome=FulfillmentOutcome.GRABBED
        )
        await _attempt(
            db_session,
            req,
            backend="slskd",
            outcome=FulfillmentOutcome.DOWNLOAD_STARTED,
        )
        await _attempt(
            db_session,
            req,
            backend="ytdlp",
            outcome=FulfillmentOutcome.SKIPPED_NOT_CONFIGURED,
        )

        data = await get_fulfillment_insights(db_session, days=30)
        assert data["failures"] == []


class TestDailyVolume:
    async def test_series_has_one_bucket_per_day_including_quiet_ones(self, db_session):
        user = await _make_user(db_session)
        await _make_request(db_session, user, source_id="today", days_ago=0)
        await _make_request(db_session, user, source_id="today-2", days_ago=0)
        await _make_request(db_session, user, source_id="older", days_ago=3)

        data = await get_fulfillment_insights(db_session, days=7)
        series = data["daily_volume"]

        assert len(series) == 7
        assert series[-1]["requests"] == 2  # today, last bucket
        assert series[-4]["requests"] == 1  # three days back
        assert series[0]["requests"] == 0  # a quiet day is present, not dropped
        assert [row["date"] for row in series] == sorted(row["date"] for row in series)

    async def test_requests_outside_the_window_are_excluded_everywhere(
        self, db_session
    ):
        user = await _make_user(db_session)
        old = await _make_request(db_session, user, source_id="ancient", days_ago=40)
        await _attempt(
            db_session,
            old,
            backend="prowlarr",
            outcome=FulfillmentOutcome.DOWNLOAD_COMPLETED,
            days_ago=40,
        )

        data = await get_fulfillment_insights(db_session, days=30)

        assert data["total_requests"] == 0
        assert sum(row["requests"] for row in data["daily_volume"]) == 0
        assert _tier(data, "prowlarr")["attempts"] == 0
        assert data["top_artists"] == []


class TestRequestSummary:
    async def test_status_counts_cover_every_status_and_span_all_users(
        self, db_session
    ):
        first = await _make_user(db_session, "one@example.com")
        second = await _make_user(db_session, "two@example.com")
        await _make_request(
            db_session, first, source_id="a", status=RequestStatus.FULFILLED
        )
        await _make_request(
            db_session, second, source_id="b", status=RequestStatus.FULFILLED
        )
        await _make_request(
            db_session, second, source_id="c", status=RequestStatus.FAILED
        )

        data = await get_fulfillment_insights(db_session, days=30)

        assert data["total_requests"] == 3
        assert data["status_counts"]["fulfilled"] == 2
        assert data["status_counts"]["failed"] == 1
        # Every status is present so the page never has to guard on a missing key.
        assert data["status_counts"]["awaiting_quality"] == 0
        assert set(data["status_counts"]) == {s.value for s in RequestStatus}

    async def test_top_artists_ranked_desc_and_tie_broken_by_name(self, db_session):
        user = await _make_user(db_session)
        for index in range(3):
            await _make_request(
                db_session, user, artist="Arijit Singh", source_id=f"as-{index}"
            )
        await _make_request(db_session, user, artist="Zara", source_id="z-1")
        await _make_request(db_session, user, artist="Anirudh", source_id="an-1")

        data = await get_fulfillment_insights(db_session, days=30)

        assert data["top_artists"] == [
            {"artist": "Arijit Singh", "requests": 3},
            # Equal counts, so ordered by name to keep the list stable between loads.
            {"artist": "Anirudh", "requests": 1},
            {"artist": "Zara", "requests": 1},
        ]

    async def test_window_metadata_describes_the_period_reported_on(self, db_session):
        data = await get_fulfillment_insights(db_session, days=14)
        expected_since = (datetime.now(UTC).date() - timedelta(days=13)).isoformat()

        assert data["window_days"] == 14
        assert data["since"] == expected_since
