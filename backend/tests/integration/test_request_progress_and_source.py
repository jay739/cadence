"""What the requests API tells a requester about a download's progress and its source.

Two user-visible questions, answered over the real routes rather than at the service layer,
because both have a failure mode that only appears there: `delivered_by` is derived from the
attempts relationship, so a route that does not eager-load it raises MissingGreenlet at
serialization time (see get_request_by_id_with_attempts), and the requests LIST is exactly
where the source badge has to appear.
"""

from __future__ import annotations

import pytest

from app.db.base import get_sessionmaker
from app.models.request import (
    FulfillmentAttempt,
    FulfillmentOutcome,
    Request,
    RequestStatus,
)
from tests.integration.conftest import register_and_login

pytestmark = pytest.mark.asyncio


def _payload(**overrides) -> dict:
    body = {
        "source": "apple",
        "source_id": "track-progress-1",
        "title": "Tum Hi Ho",
        "artist": "Arijit Singh",
        "album": "Aashiqui 2",
        "duration_seconds": 262,
        "artwork_url": None,
        "musicbrainz_recording_id": None,
    }
    body.update(overrides)
    return body


async def _drive_request(request_id: str, *, status: RequestStatus, **fields) -> None:
    """Put a request into a given state directly, standing in for the worker.

    The worker itself is not running in these tests, so this writes exactly what it would have
    written: a status, whatever progress the tier reported, and the audit rows behind it.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        request_row = await session.get(Request, request_id)
        assert request_row is not None
        request_row.status = status
        for key, value in fields.items():
            setattr(request_row, key, value)
        await session.commit()


async def _append_attempt(
    request_id: str, backend: str, outcome: FulfillmentOutcome
) -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        session.add(
            FulfillmentAttempt(
                request_id=request_id,
                backend=backend,
                query_used="Arijit Singh Tum Hi Ho",
                candidates_considered=None,
                chosen_candidate=None,
                outcome=outcome.value,
            )
        )
        await session.commit()


async def _create(app_client, headers, **overrides) -> str:
    created = await app_client.post(
        "/api/requests", json=_payload(**overrides), headers=headers
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


class TestDownloadProgressOverTheApi:
    async def test_a_torrent_download_reports_its_real_percentage(self, app_client):
        headers = await register_and_login(
            app_client, "progress1@example.com", "a-strong-password-1"
        )
        request_id = await _create(app_client, headers)
        await _drive_request(
            request_id,
            status=RequestStatus.DOWNLOADING,
            download_progress_percent=63.5,
            download_phase="Downloading from the torrent swarm",
        )

        detail = await app_client.get(f"/api/requests/{request_id}", headers=headers)
        body = detail.json()
        assert body["download_progress_percent"] == pytest.approx(63.5)
        assert body["download_phase"] == "Downloading from the torrent swarm"

    async def test_the_youtube_tier_reports_a_phase_and_no_number(self, app_client):
        """The tier with nothing to measure must send null, never a stand-in percentage.

        A 0 here would render as a bar sitting at the far left, which reads as "started but
        nothing has arrived" rather than the truth, "this source cannot report progress".
        """
        headers = await register_and_login(
            app_client, "progress2@example.com", "a-strong-password-1"
        )
        request_id = await _create(app_client, headers)
        await _drive_request(
            request_id,
            status=RequestStatus.DOWNLOADING,
            download_progress_percent=None,
            download_phase="Fetching the audio from YouTube",
        )

        detail = await app_client.get(f"/api/requests/{request_id}", headers=headers)
        body = detail.json()
        assert body["download_progress_percent"] is None
        assert body["download_phase"] == "Fetching the audio from YouTube"

    async def test_progress_is_visible_in_the_requests_list_too(self, app_client):
        """The list is where a requester watches several downloads at once."""
        headers = await register_and_login(
            app_client, "progress3@example.com", "a-strong-password-1"
        )
        request_id = await _create(app_client, headers)
        await _drive_request(
            request_id,
            status=RequestStatus.DOWNLOADING,
            download_progress_percent=12.0,
            download_phase="Receiving from a Soulseek peer",
        )

        listing = await app_client.get("/api/requests/mine", headers=headers)
        row = next(r for r in listing.json() if r["id"] == request_id)
        assert row["download_progress_percent"] == pytest.approx(12.0)
        assert row["download_phase"] == "Receiving from a Soulseek peer"

    async def test_a_fresh_request_has_no_progress_at_all(self, app_client):
        headers = await register_and_login(
            app_client, "progress4@example.com", "a-strong-password-1"
        )
        request_id = await _create(app_client, headers)

        detail = await app_client.get(f"/api/requests/{request_id}", headers=headers)
        body = detail.json()
        assert body["download_progress_percent"] is None
        assert body["download_phase"] is None


class TestDeliveredSourceOverTheApi:
    async def test_a_fulfilled_request_names_the_tier_that_delivered_it(
        self, app_client
    ):
        headers = await register_and_login(
            app_client, "source1@example.com", "a-strong-password-1"
        )
        request_id = await _create(app_client, headers)
        await _append_attempt(request_id, "prowlarr", FulfillmentOutcome.GRABBED)
        await _append_attempt(
            request_id, "prowlarr", FulfillmentOutcome.DOWNLOAD_COMPLETED
        )
        await _drive_request(request_id, status=RequestStatus.FULFILLED)

        detail = await app_client.get(f"/api/requests/{request_id}", headers=headers)
        assert detail.json()["delivered_by"] == "prowlarr"

    async def test_a_later_tier_that_rescued_the_request_gets_the_credit(
        self, app_client
    ):
        """Prowlarr found nothing, Soulseek delivered. The badge must say Soulseek.

        Guards the derivation reading the first attempt (prowlarr) rather than the last
        completion, which would name a tier that never produced the file.
        """
        headers = await register_and_login(
            app_client, "source2@example.com", "a-strong-password-1"
        )
        request_id = await _create(app_client, headers)
        await _append_attempt(
            request_id, "prowlarr", FulfillmentOutcome.SEARCHED_NO_CANDIDATES
        )
        await _append_attempt(request_id, "slskd", FulfillmentOutcome.DOWNLOAD_STARTED)
        await _append_attempt(
            request_id, "slskd", FulfillmentOutcome.DOWNLOAD_COMPLETED
        )
        await _drive_request(request_id, status=RequestStatus.FULFILLED)

        detail = await app_client.get(f"/api/requests/{request_id}", headers=headers)
        assert detail.json()["delivered_by"] == "slskd"

    async def test_the_requests_list_carries_the_source_without_a_greenlet_error(
        self, app_client
    ):
        """The list route must eager-load attempts, or this 500s instead of answering.

        This is the specific regression the eager load on list_requests_for_user exists for:
        serializing a derived-from-relationship field on a lazily-loaded row raises
        MissingGreenlet outside the session's greenlet context.
        """
        headers = await register_and_login(
            app_client, "source3@example.com", "a-strong-password-1"
        )
        request_id = await _create(app_client, headers)
        await _append_attempt(
            request_id, "ytdlp", FulfillmentOutcome.DOWNLOAD_COMPLETED
        )
        await _drive_request(request_id, status=RequestStatus.FULFILLED)

        listing = await app_client.get("/api/requests/mine", headers=headers)
        assert listing.status_code == 200, listing.text
        row = next(r for r in listing.json() if r["id"] == request_id)
        assert row["delivered_by"] == "ytdlp"

    async def test_a_request_that_has_delivered_nothing_names_no_source(
        self, app_client
    ):
        headers = await register_and_login(
            app_client, "source4@example.com", "a-strong-password-1"
        )
        request_id = await _create(app_client, headers)
        await _append_attempt(
            request_id, "prowlarr", FulfillmentOutcome.SEARCHED_NO_CANDIDATES
        )

        detail = await app_client.get(f"/api/requests/{request_id}", headers=headers)
        assert detail.json()["delivered_by"] is None
