from __future__ import annotations

import pytest

from tests.integration.conftest import make_admin_headers, register_and_login

pytestmark = pytest.mark.asyncio


def _payload(**overrides):
    body = {
        "source": "apple",
        "source_id": "track-abc",
        "title": "Tum Hi Ho",
        "artist": "Arijit Singh",
        "album": "Aashiqui 2",
    }
    body.update(overrides)
    return body


class TestCreateRequest:
    async def test_create_requires_auth(self, app_client):
        resp = await app_client.post("/api/requests", json=_payload())
        assert resp.status_code == 401

    async def test_create_request_succeeds(self, app_client):
        headers = await register_and_login(
            app_client, "req1@example.com", "a-strong-password-1"
        )
        resp = await app_client.post("/api/requests", json=_payload(), headers=headers)
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "pending"
        assert body["title"] == "Tum Hi Ho"

    async def test_create_request_rejects_invalid_source(self, app_client):
        headers = await register_and_login(
            app_client, "req2@example.com", "a-strong-password-1"
        )
        resp = await app_client.post(
            "/api/requests", json=_payload(source="not-a-real-source"), headers=headers
        )
        assert resp.status_code == 422

    async def test_create_request_rejects_missing_title(self, app_client):
        headers = await register_and_login(
            app_client, "req3@example.com", "a-strong-password-1"
        )
        payload = _payload()
        del payload["title"]
        resp = await app_client.post("/api/requests", json=payload, headers=headers)
        assert resp.status_code == 422

    async def test_duplicate_request_is_idempotent(self, app_client):
        headers = await register_and_login(
            app_client, "req4@example.com", "a-strong-password-1"
        )
        first = await app_client.post("/api/requests", json=_payload(), headers=headers)
        second = await app_client.post(
            "/api/requests", json=_payload(), headers=headers
        )
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]

    async def test_different_users_requesting_same_track_get_separate_rows(
        self, app_client
    ):
        headers1 = await register_and_login(
            app_client, "usera@example.com", "a-strong-password-1"
        )
        headers2 = await register_and_login(
            app_client, "userb@example.com", "a-strong-password-1"
        )
        r1 = await app_client.post("/api/requests", json=_payload(), headers=headers1)
        r2 = await app_client.post("/api/requests", json=_payload(), headers=headers2)
        assert r1.json()["id"] != r2.json()["id"]


class TestMyRequests:
    async def test_mine_only_shows_own_requests(self, app_client):
        headers1 = await register_and_login(
            app_client, "mine1@example.com", "a-strong-password-1"
        )
        headers2 = await register_and_login(
            app_client, "mine2@example.com", "a-strong-password-1"
        )
        await app_client.post("/api/requests", json=_payload(), headers=headers1)
        await app_client.post(
            "/api/requests", json=_payload(source_id="other-track"), headers=headers2
        )
        resp = await app_client.get("/api/requests/mine", headers=headers1)
        assert resp.status_code == 200
        assert len(resp.json()) == 1


class TestRequestDetail:
    async def test_owner_can_view_detail(self, app_client):
        headers = await register_and_login(
            app_client, "detail1@example.com", "a-strong-password-1"
        )
        created = await app_client.post(
            "/api/requests", json=_payload(), headers=headers
        )
        request_id = created.json()["id"]
        resp = await app_client.get(f"/api/requests/{request_id}", headers=headers)
        assert resp.status_code == 200
        assert "attempts" in resp.json()

    async def test_other_user_cannot_view_detail(self, app_client):
        headers1 = await register_and_login(
            app_client, "detail2@example.com", "a-strong-password-1"
        )
        headers2 = await register_and_login(
            app_client, "detail3@example.com", "a-strong-password-1"
        )
        created = await app_client.post(
            "/api/requests", json=_payload(), headers=headers1
        )
        request_id = created.json()["id"]
        resp = await app_client.get(f"/api/requests/{request_id}", headers=headers2)
        assert resp.status_code == 404

    async def test_nonexistent_request_returns_404(self, app_client):
        headers = await register_and_login(
            app_client, "detail4@example.com", "a-strong-password-1"
        )
        resp = await app_client.get("/api/requests/does-not-exist", headers=headers)
        assert resp.status_code == 404


class TestAdminApprovalQueue:
    async def test_non_admin_cannot_see_pending_queue(self, app_client):
        headers = await register_and_login(
            app_client, "notadmin1@example.com", "a-strong-password-1"
        )
        resp = await app_client.get("/api/requests/admin/pending", headers=headers)
        assert resp.status_code == 403

    async def test_non_admin_cannot_approve(self, app_client):
        headers = await register_and_login(
            app_client, "notadmin2@example.com", "a-strong-password-1"
        )
        created = await app_client.post(
            "/api/requests", json=_payload(), headers=headers
        )
        request_id = created.json()["id"]
        resp = await app_client.post(
            f"/api/requests/{request_id}/approve", json={"note": "ok"}, headers=headers
        )
        assert resp.status_code == 403

    async def test_admin_can_see_pending_queue(self, app_client):
        user_headers = await register_and_login(
            app_client, "userforadmin@example.com", "a-strong-password-1"
        )
        await app_client.post("/api/requests", json=_payload(), headers=user_headers)
        admin_headers = await make_admin_headers(app_client)
        resp = await app_client.get(
            "/api/requests/admin/pending", headers=admin_headers
        )
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    async def test_admin_can_approve_request(self, app_client):
        user_headers = await register_and_login(
            app_client, "userforapprove@example.com", "a-strong-password-1"
        )
        created = await app_client.post(
            "/api/requests", json=_payload(), headers=user_headers
        )
        request_id = created.json()["id"]
        admin_headers = await make_admin_headers(app_client)
        resp = await app_client.post(
            f"/api/requests/{request_id}/approve",
            json={"note": "go ahead"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"
        assert resp.json()["admin_note"] == "go ahead"

    async def test_admin_can_reject_request(self, app_client):
        user_headers = await register_and_login(
            app_client, "userforreject@example.com", "a-strong-password-1"
        )
        created = await app_client.post(
            "/api/requests", json=_payload(), headers=user_headers
        )
        request_id = created.json()["id"]
        admin_headers = await make_admin_headers(app_client)
        resp = await app_client.post(
            f"/api/requests/{request_id}/reject",
            json={"note": "duplicate"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    async def test_rejection_visible_to_requesting_user(self, app_client):
        user_headers = await register_and_login(
            app_client, "userforvisible@example.com", "a-strong-password-1"
        )
        created = await app_client.post(
            "/api/requests", json=_payload(), headers=user_headers
        )
        request_id = created.json()["id"]
        admin_headers = await make_admin_headers(app_client)
        await app_client.post(
            f"/api/requests/{request_id}/reject",
            json={"note": "no thanks"},
            headers=admin_headers,
        )
        resp = await app_client.get("/api/requests/mine", headers=user_headers)
        matching = [r for r in resp.json() if r["id"] == request_id]
        assert matching[0]["status"] == "rejected"
        assert matching[0]["admin_note"] == "no thanks"

    async def test_rejected_then_resubmitted_becomes_pending_again(self, app_client):
        user_headers = await register_and_login(
            app_client, "userresubmit@example.com", "a-strong-password-1"
        )
        created = await app_client.post(
            "/api/requests", json=_payload(), headers=user_headers
        )
        request_id = created.json()["id"]
        admin_headers = await make_admin_headers(app_client)
        await app_client.post(
            f"/api/requests/{request_id}/reject",
            json={"note": "no"},
            headers=admin_headers,
        )
        resubmitted = await app_client.post(
            "/api/requests", json=_payload(), headers=user_headers
        )
        assert resubmitted.json()["id"] == request_id
        assert resubmitted.json()["status"] == "pending"

    async def test_admin_all_requests_lists_everything(self, app_client):
        user_headers = await register_and_login(
            app_client, "userall@example.com", "a-strong-password-1"
        )
        await app_client.post("/api/requests", json=_payload(), headers=user_headers)
        admin_headers = await make_admin_headers(app_client)
        resp = await app_client.get("/api/requests/admin/all", headers=admin_headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    async def test_admin_approving_nonexistent_request_404s(self, app_client):
        admin_headers = await make_admin_headers(app_client)
        resp = await app_client.post(
            "/api/requests/does-not-exist/approve",
            json={"note": "x"},
            headers=admin_headers,
        )
        assert resp.status_code == 404
