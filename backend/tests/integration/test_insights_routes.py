"""Auth gating and end-to-end shape of GET /api/admin/insights.

The gate is the point of this file: the endpoint reports on every user's requests, so a general
user must get a flat 403 rather than a narrowed-down view of their own data.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.base import get_sessionmaker
from app.models.request import FulfillmentAttempt, FulfillmentOutcome, Request
from tests.integration.conftest import (
    make_admin_headers,
    make_navidrome_headers,
    register_and_login,
)

pytestmark = pytest.mark.asyncio


def _payload(**overrides):
    body = {
        "source": "apple",
        "source_id": "insight-track",
        "title": "Kesariya",
        "artist": "Arijit Singh",
        "album": "Brahmastra",
    }
    body.update(overrides)
    return body


async def _record_attempt(
    request_id: str, *, backend: str, outcome: FulfillmentOutcome
) -> None:
    """Write an audit row the way the fulfillment worker would, without running one."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        row = (
            await session.execute(select(Request).where(Request.id == request_id))
        ).scalar_one()
        session.add(
            FulfillmentAttempt(
                request_id=row.id,
                backend=backend,
                query_used=f"{row.artist} {row.title}",
                outcome=outcome.value,
            )
        )
        await session.commit()


class TestAuth:
    async def test_requires_auth(self, app_client):
        resp = await app_client.get("/api/admin/insights")
        assert resp.status_code == 401

    async def test_general_user_is_forbidden(self, app_client):
        headers = await register_and_login(
            app_client, "notadmin@example.com", "a-strong-password-1"
        )
        resp = await app_client.get("/api/admin/insights", headers=headers)
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Admin access required"

    async def test_general_navidrome_user_is_also_forbidden(self, app_client):
        """Role, not auth provider, is what the gate reads."""
        headers = await make_navidrome_headers(app_client)
        resp = await app_client.get("/api/admin/insights", headers=headers)
        assert resp.status_code == 403

    async def test_admin_is_allowed(self, app_client):
        headers = await make_admin_headers(app_client)
        resp = await app_client.get("/api/admin/insights", headers=headers)
        assert resp.status_code == 200


class TestWindowParam:
    async def test_defaults_to_thirty_days(self, app_client):
        headers = await make_admin_headers(app_client)
        body = (await app_client.get("/api/admin/insights", headers=headers)).json()
        assert body["window_days"] == 30
        assert len(body["daily_volume"]) == 30

    async def test_honours_an_explicit_window(self, app_client):
        headers = await make_admin_headers(app_client)
        body = (
            await app_client.get("/api/admin/insights?days=7", headers=headers)
        ).json()
        assert body["window_days"] == 7
        assert len(body["daily_volume"]) == 7

    @pytest.mark.parametrize("days", [0, -1, 366, "abc"])
    async def test_rejects_an_out_of_range_window(self, app_client, days):
        headers = await make_admin_headers(app_client)
        resp = await app_client.get(f"/api/admin/insights?days={days}", headers=headers)
        assert resp.status_code == 422


class TestReportedNumbers:
    async def test_reports_real_requests_and_attempts(self, app_client):
        user_headers = await register_and_login(
            app_client, "insightuser@example.com", "a-strong-password-1"
        )
        delivered = await app_client.post(
            "/api/requests", json=_payload(source_id="delivered"), headers=user_headers
        )
        assert delivered.status_code == 201
        failed = await app_client.post(
            "/api/requests",
            json=_payload(source_id="nothing-found"),
            headers=user_headers,
        )

        await _record_attempt(
            delivered.json()["id"],
            backend="prowlarr",
            outcome=FulfillmentOutcome.GRABBED,
        )
        await _record_attempt(
            delivered.json()["id"],
            backend="prowlarr",
            outcome=FulfillmentOutcome.DOWNLOAD_COMPLETED,
        )
        await _record_attempt(
            failed.json()["id"],
            backend="prowlarr",
            outcome=FulfillmentOutcome.SEARCHED_NO_CANDIDATES,
        )

        admin_headers = await make_admin_headers(app_client)
        body = (
            await app_client.get("/api/admin/insights", headers=admin_headers)
        ).json()

        prowlarr = next(t for t in body["tiers"] if t["backend"] == "prowlarr")
        assert prowlarr["attempts"] == 3
        assert prowlarr["requests_tried"] == 2
        assert prowlarr["requests_delivered"] == 1
        assert prowlarr["success_rate"] == 0.5
        assert body["failures"] == [
            {
                "outcome": "searched_no_candidates",
                "count": 1,
                "backends": {"prowlarr": 1},
            }
        ]

        # Both requests were created today, so they land in the final daily bucket.
        assert body["daily_volume"][-1]["requests"] == 2
        assert body["total_requests"] == 2
        assert body["status_counts"]["pending"] == 2
        assert body["top_artists"] == [{"artist": "Arijit Singh", "requests": 2}]

    async def test_an_empty_install_reports_zeroes_rather_than_failing(
        self, app_client
    ):
        headers = await make_admin_headers(app_client)
        body = (await app_client.get("/api/admin/insights", headers=headers)).json()

        assert body["total_requests"] == 0
        assert body["failures"] == []
        assert body["top_artists"] == []
        assert [t["backend"] for t in body["tiers"]] == ["prowlarr", "slskd", "ytdlp"]
        assert all(t["success_rate"] is None for t in body["tiers"])


class TestDemoMode:
    async def test_the_seeded_demo_queue_produces_a_populated_report(
        self, demo_app_client
    ):
        """Demo mode is what the screenshots and first-run evaluation are taken from, so an
        insight page that renders empty there would read as a broken feature.
        """
        headers = await make_admin_headers(demo_app_client)
        body = (
            await demo_app_client.get("/api/admin/insights", headers=headers)
        ).json()

        assert body["total_requests"] >= 7
        assert body["top_artists"]
        assert body["failures"]
        prowlarr = next(t for t in body["tiers"] if t["backend"] == "prowlarr")
        # The seeded fulfilled request carries the completion row the real worker writes, so
        # the demo shows a genuine delivery rate rather than a flat 0%.
        assert prowlarr["requests_delivered"] == 1
        assert prowlarr["success_rate"] is not None and prowlarr["success_rate"] > 0
        # Seeded across several days, so the volume series is a history, not one spike.
        assert sum(1 for day in body["daily_volume"] if day["requests"]) >= 3
