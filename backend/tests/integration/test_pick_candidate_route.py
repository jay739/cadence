"""Integration tests for POST /api/requests/{id}/pick-candidate.

Covers the route's own responsibilities: access (owner-or-admin, 404 for anyone else), the
status guard, the specific-message refusals, and that a successful pick actually re-queues the
request with the candidate pinned.
"""

from __future__ import annotations

import pytest

from app.models.request import RequestStatus
from tests.integration.conftest import make_admin_headers, register_and_login

pytestmark = pytest.mark.asyncio


async def _add_attempt(
    request_id: str, candidates: list, *, backend: str = "ytdlp"
) -> str:
    """Write one FulfillmentAttempt row directly, the way a real fulfillment pass would.

    Direct DB writes for the same reason the retry tests force status directly (see
    _force_status in test_requests_routes.py): the background worker's loop is scheduled on a
    different event loop than the test body, so nothing here can wait on a real pass.
    """
    from app.db.base import get_sessionmaker
    from app.models.request import FulfillmentAttempt

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        attempt = FulfillmentAttempt(
            request_id=request_id,
            backend=backend,
            query_used="Sooraj Santhosh Labbar Bomma",
            candidates_considered=candidates,
            chosen_candidate=candidates[0] if candidates else None,
            outcome="download_failed",
            detail="ytdlp-shim job ended in state 'failed'",
        )
        session.add(attempt)
        await session.commit()
        await session.refresh(attempt)
        return attempt.id


async def _force_status(request_id: str, new_status) -> None:
    from sqlalchemy import select

    from app.db.base import get_sessionmaker
    from app.models.request import Request

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(select(Request).where(Request.id == request_id))
        row = result.scalar_one()
        row.status = new_status
        await session.commit()


async def _read_request(request_id: str):
    from sqlalchemy import select

    from app.db.base import get_sessionmaker
    from app.models.request import Request

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(select(Request).where(Request.id == request_id))
        return result.scalar_one()


def _candidates() -> list:
    """Ten YouTube candidates, as in the real failure: the top-scoring one was the wrong song."""
    return [
        {
            "backend": "ytdlp",
            "identifier": "wrongpick01",
            "name": "Labbar Bomma Full Song Audio Jukebox",
            "similarity": 0.685,
            "quality": "lossy",
            "extra": {"uploader": "Random Uploads"},
        },
        {
            "backend": "ytdlp",
            "identifier": "rightpick02",
            "name": "Sooraj Santhosh, Ranina Reddy - Labbar Bomma",
            "similarity": 0.612,
            "quality": "lossy",
            "extra": {"uploader": "Aditya Music"},
        },
    ]


async def _failed_request_with_candidates(
    app_client, email: str, source_id: str, *, quality: str = "any"
):
    headers = await register_and_login(app_client, email, "a-strong-password-1")
    created = await app_client.post(
        "/api/requests",
        json={
            "source": "apple",
            "source_id": source_id,
            "title": "Labbar Bomma",
            "artist": "Sooraj Santhosh",
            "desired_quality": quality,
        },
        headers=headers,
    )
    assert created.status_code == 201
    request_id = created.json()["id"]
    attempt_id = await _add_attempt(request_id, _candidates())
    await _force_status(request_id, RequestStatus.FAILED)
    return headers, request_id, attempt_id


class TestPickCandidateAccess:
    async def test_requires_auth(self, app_client):
        resp = await app_client.post(
            "/api/requests/does-not-exist/pick-candidate",
            json={"attempt_id": "x", "candidate_index": 0},
        )
        assert resp.status_code == 401

    async def test_owner_can_override_the_pick_on_their_own_failed_request(
        self, app_client
    ):
        headers, request_id, attempt_id = await _failed_request_with_candidates(
            app_client, "pickowner@example.com", "pick-1"
        )

        resp = await app_client.post(
            f"/api/requests/{request_id}/pick-candidate",
            json={"attempt_id": attempt_id, "candidate_index": 1},
            headers=headers,
        )

        assert resp.status_code == 200
        body = resp.json()
        # Re-queued, exactly like retry and accept-lossy do, so the worker picks it up.
        assert body["status"] == "approved"
        assert body["failure_reason"] is None

        # And the candidate the requester chose is what got pinned, not the one scoring chose.
        row = await _read_request(request_id)
        assert row.manual_candidate["identifier"] == "rightpick02"
        assert row.manual_candidate["backend"] == "ytdlp"

    async def test_admin_can_override_someone_elses_pick(self, app_client):
        _headers, request_id, attempt_id = await _failed_request_with_candidates(
            app_client, "pickowner2@example.com", "pick-2"
        )
        admin_headers = await make_admin_headers(app_client)

        resp = await app_client.post(
            f"/api/requests/{request_id}/pick-candidate",
            json={"attempt_id": attempt_id, "candidate_index": 1},
            headers=admin_headers,
        )

        assert resp.status_code == 200

    async def test_a_different_user_cannot_pick_and_is_not_told_it_exists(
        self, app_client
    ):
        _owner, request_id, attempt_id = await _failed_request_with_candidates(
            app_client, "pickowner3@example.com", "pick-3"
        )
        stranger_headers = await register_and_login(
            app_client, "pickstranger@example.com", "a-strong-password-1"
        )

        resp = await app_client.post(
            f"/api/requests/{request_id}/pick-candidate",
            json={"attempt_id": attempt_id, "candidate_index": 1},
            headers=stranger_headers,
        )

        # 404, not 403: matches get_request_detail's refusal to confirm another user's
        # request even exists.
        assert resp.status_code == 404
        # Nothing was pinned by the attempt.
        row = await _read_request(request_id)
        assert row.manual_candidate is None

    async def test_nonexistent_request_404s(self, app_client):
        headers = await register_and_login(
            app_client, "pick404@example.com", "a-strong-password-1"
        )
        resp = await app_client.post(
            "/api/requests/does-not-exist/pick-candidate",
            json={"attempt_id": "att-1", "candidate_index": 0},
            headers=headers,
        )
        assert resp.status_code == 404


class TestPickCandidateStatusGuard:
    async def test_a_fulfilled_request_cannot_be_re_picked(self, app_client):
        """It would overwrite the file already sitting in the library at final_file_path."""
        headers, request_id, attempt_id = await _failed_request_with_candidates(
            app_client, "pickfulfilled@example.com", "pick-4"
        )
        await _force_status(request_id, RequestStatus.FULFILLED)

        resp = await app_client.post(
            f"/api/requests/{request_id}/pick-candidate",
            json={"attempt_id": attempt_id, "candidate_index": 1},
            headers=headers,
        )

        assert resp.status_code == 409
        assert "fulfilled" in resp.json()["detail"]
        row = await _read_request(request_id)
        assert row.manual_candidate is None

    async def test_a_downloading_request_cannot_be_re_picked(self, app_client):
        """A transfer is in flight and the worker holds the row."""
        headers, request_id, attempt_id = await _failed_request_with_candidates(
            app_client, "pickdownloading@example.com", "pick-5"
        )
        await _force_status(request_id, RequestStatus.DOWNLOADING)

        resp = await app_client.post(
            f"/api/requests/{request_id}/pick-candidate",
            json={"attempt_id": attempt_id, "candidate_index": 1},
            headers=headers,
        )

        assert resp.status_code == 409
        assert "downloading" in resp.json()["detail"]

    async def test_a_rejected_request_cannot_be_re_picked(self, app_client):
        """An admin decision must not be routed around by picking a candidate."""
        headers, request_id, attempt_id = await _failed_request_with_candidates(
            app_client, "pickrejected@example.com", "pick-6"
        )
        await _force_status(request_id, RequestStatus.REJECTED)

        resp = await app_client.post(
            f"/api/requests/{request_id}/pick-candidate",
            json={"attempt_id": attempt_id, "candidate_index": 1},
            headers=headers,
        )

        assert resp.status_code == 409

    async def test_an_awaiting_quality_request_can_be_re_picked(self, app_client):
        headers, request_id, attempt_id = await _failed_request_with_candidates(
            app_client, "pickawaiting@example.com", "pick-7"
        )
        await _force_status(request_id, RequestStatus.AWAITING_QUALITY)

        resp = await app_client.post(
            f"/api/requests/{request_id}/pick-candidate",
            json={"attempt_id": attempt_id, "candidate_index": 1},
            headers=headers,
        )

        assert resp.status_code == 200


class TestPickCandidateRejectsBadPayloads:
    async def test_an_unknown_attempt_id_is_a_422_with_a_specific_message(
        self, app_client
    ):
        headers, request_id, _attempt_id = await _failed_request_with_candidates(
            app_client, "pickbadattempt@example.com", "pick-8"
        )

        resp = await app_client.post(
            f"/api/requests/{request_id}/pick-candidate",
            json={
                "attempt_id": "00000000-0000-0000-0000-000000000000",
                "candidate_index": 0,
            },
            headers=headers,
        )

        assert resp.status_code == 422
        assert "not part of this request's history" in resp.json()["detail"]

    async def test_an_out_of_range_index_is_a_422_naming_the_real_count(
        self, app_client
    ):
        headers, request_id, attempt_id = await _failed_request_with_candidates(
            app_client, "pickbadindex@example.com", "pick-9"
        )

        resp = await app_client.post(
            f"/api/requests/{request_id}/pick-candidate",
            json={"attempt_id": attempt_id, "candidate_index": 9},
            headers=headers,
        )

        assert resp.status_code == 422
        assert "2 candidate(s)" in resp.json()["detail"]

    async def test_a_negative_index_is_rejected_by_the_schema(self, app_client):
        headers, request_id, attempt_id = await _failed_request_with_candidates(
            app_client, "picknegative@example.com", "pick-10"
        )

        resp = await app_client.post(
            f"/api/requests/{request_id}/pick-candidate",
            json={"attempt_id": attempt_id, "candidate_index": -1},
            headers=headers,
        )

        assert resp.status_code == 422

    async def test_a_candidate_with_no_identifier_is_refused_with_the_fix(
        self, app_client
    ):
        """Rows written before candidates carried an identifier cannot be acted on."""
        headers = await register_and_login(
            app_client, "pickoldrow@example.com", "a-strong-password-1"
        )
        created = await app_client.post(
            "/api/requests",
            json={
                "source": "apple",
                "source_id": "pick-11",
                "title": "Labbar Bomma",
                "artist": "Sooraj Santhosh",
            },
            headers=headers,
        )
        request_id = created.json()["id"]
        # The old display-only summary shape: name and similarity, no identifier.
        attempt_id = await _add_attempt(
            request_id, [{"name": "Labbar Bomma Audio", "similarity": 0.68}]
        )
        await _force_status(request_id, RequestStatus.FAILED)

        resp = await app_client.post(
            f"/api/requests/{request_id}/pick-candidate",
            json={"attempt_id": attempt_id, "candidate_index": 0},
            headers=headers,
        )

        assert resp.status_code == 422
        assert "Retry the request to search again" in resp.json()["detail"]

    async def test_a_candidate_naming_an_unknown_backend_is_refused(self, app_client):
        headers = await register_and_login(
            app_client, "pickbadbackend@example.com", "a-strong-password-1"
        )
        created = await app_client.post(
            "/api/requests",
            json={
                "source": "apple",
                "source_id": "pick-12",
                "title": "Labbar Bomma",
                "artist": "Sooraj Santhosh",
            },
            headers=headers,
        )
        request_id = created.json()["id"]
        attempt_id = await _add_attempt(
            request_id,
            [
                {
                    "backend": "attacker-controlled",
                    "identifier": "http://169.254.169.254/latest/meta-data/",
                    "name": "Not a real tier",
                }
            ],
        )
        await _force_status(request_id, RequestStatus.FAILED)

        resp = await app_client.post(
            f"/api/requests/{request_id}/pick-candidate",
            json={"attempt_id": attempt_id, "candidate_index": 0},
            headers=headers,
        )

        assert resp.status_code == 422
        assert "not a download source" in resp.json()["detail"]
        row = await _read_request(request_id)
        assert row.manual_candidate is None


class TestPickCandidateHonoursTheLosslessContract:
    async def test_hand_picking_a_lossy_candidate_for_a_lossless_request_is_refused(
        self, app_client
    ):
        """Picking by hand must not be a way around the quality contract."""
        headers, request_id, attempt_id = await _failed_request_with_candidates(
            app_client, "picklossless@example.com", "pick-13", quality="lossless"
        )

        resp = await app_client.post(
            f"/api/requests/{request_id}/pick-candidate",
            json={"attempt_id": attempt_id, "candidate_index": 1},
            headers=headers,
        )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "lossless" in detail
        # Points at the existing explicit-consent path rather than dead-ending.
        assert "Accept a lossy copy" in detail

        row = await _read_request(request_id)
        assert row.manual_candidate is None
        assert row.status == RequestStatus.FAILED

    async def test_the_same_pick_is_allowed_once_a_lossy_copy_has_been_accepted(
        self, app_client
    ):
        """Routed through the existing consent path, the pick goes ahead."""
        headers, request_id, attempt_id = await _failed_request_with_candidates(
            app_client, "picksettled@example.com", "pick-14", quality="lossless"
        )
        await _force_status(request_id, RequestStatus.AWAITING_QUALITY)

        accepted = await app_client.post(
            f"/api/requests/{request_id}/accept-lossy", headers=headers
        )
        assert accepted.status_code == 200

        resp = await app_client.post(
            f"/api/requests/{request_id}/pick-candidate",
            json={"attempt_id": attempt_id, "candidate_index": 1},
            headers=headers,
        )

        assert resp.status_code == 200
        row = await _read_request(request_id)
        assert row.manual_candidate["identifier"] == "rightpick02"

    async def test_a_lossless_candidate_is_accepted_for_a_lossless_request(
        self, app_client
    ):
        headers = await register_and_login(
            app_client, "picklosslessok@example.com", "a-strong-password-1"
        )
        created = await app_client.post(
            "/api/requests",
            json={
                "source": "apple",
                "source_id": "pick-15",
                "title": "Labbar Bomma",
                "artist": "Sooraj Santhosh",
                "desired_quality": "lossless",
            },
            headers=headers,
        )
        request_id = created.json()["id"]
        attempt_id = await _add_attempt(
            request_id,
            [
                {
                    "backend": "prowlarr",
                    "identifier": "guid-flac-1",
                    "name": "Sooraj Santhosh - Labbar Bomma [FLAC]",
                    "similarity": 0.81,
                    "quality": "lossless",
                    "extra": {"indexerId": 3},
                }
            ],
            backend="prowlarr",
        )
        await _force_status(request_id, RequestStatus.FAILED)

        resp = await app_client.post(
            f"/api/requests/{request_id}/pick-candidate",
            json={"attempt_id": attempt_id, "candidate_index": 0},
            headers=headers,
        )

        assert resp.status_code == 200
        row = await _read_request(request_id)
        assert row.manual_candidate["identifier"] == "guid-flac-1"
