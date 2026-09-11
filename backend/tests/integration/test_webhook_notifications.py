"""Integration tests for user-configurable webhook notifications.

Covers: the PATCH /api/auth/me/webhook endpoint (set/clear, format validation), that a webhook
fires on the approve transition and not on reject, and -- the most important property of this
feature -- that a webhook pointed at a private/internal address never results in a real
outbound call and never affects the request's own status, even though the URL was accepted at
save time (format-only validation; the SSRF guard lives in app.core.safe_http and runs at
delivery time, see app.services.webhook_delivery).
"""

from __future__ import annotations

import json

import httpx
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


class TestSetWebhookUrl:
    async def test_requires_auth(self, app_client):
        resp = await app_client.patch(
            "/api/auth/me/webhook", json={"webhook_url": "https://example.com/hook"}
        )
        assert resp.status_code == 401

    async def test_sets_a_valid_https_url(self, app_client):
        headers = await register_and_login(
            app_client, "webhook-set@example.com", "a-strong-password-1"
        )
        resp = await app_client.patch(
            "/api/auth/me/webhook",
            json={"webhook_url": "https://example.com/hook"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["webhook_url"] == "https://example.com/hook"

        me = await app_client.get("/api/auth/me", headers=headers)
        assert me.json()["webhook_url"] == "https://example.com/hook"

    async def test_sets_a_valid_http_url(self, app_client):
        headers = await register_and_login(
            app_client, "webhook-http@example.com", "a-strong-password-1"
        )
        resp = await app_client.patch(
            "/api/auth/me/webhook",
            json={"webhook_url": "http://example.com/hook"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["webhook_url"] == "http://example.com/hook"

    async def test_clears_via_null(self, app_client):
        headers = await register_and_login(
            app_client, "webhook-clear@example.com", "a-strong-password-1"
        )
        await app_client.patch(
            "/api/auth/me/webhook",
            json={"webhook_url": "https://example.com/hook"},
            headers=headers,
        )
        resp = await app_client.patch(
            "/api/auth/me/webhook", json={"webhook_url": None}, headers=headers
        )
        assert resp.status_code == 200
        assert resp.json()["webhook_url"] is None

    async def test_rejects_non_http_scheme_with_specific_422(self, app_client):
        headers = await register_and_login(
            app_client, "webhook-badscheme@example.com", "a-strong-password-1"
        )
        resp = await app_client.patch(
            "/api/auth/me/webhook",
            json={"webhook_url": "ftp://example.com/hook"},
            headers=headers,
        )
        assert resp.status_code == 422
        # This app's global RequestValidationError handler (app/main.py) reshapes the response
        # to {"detail": "Invalid request", "errors": [...]}, not FastAPI's default
        # {"detail": [...]} -- see the "errors" key, not "detail", for the per-field messages.
        errors = resp.json()["errors"]
        assert any("http" in str(err.get("msg", "")).lower() for err in errors)

    async def test_rejects_javascript_scheme_with_specific_422(self, app_client):
        headers = await register_and_login(
            app_client, "webhook-badscheme2@example.com", "a-strong-password-1"
        )
        resp = await app_client.patch(
            "/api/auth/me/webhook",
            json={"webhook_url": "javascript:alert(1)"},
            headers=headers,
        )
        assert resp.status_code == 422

    async def test_rejects_not_a_url_at_all(self, app_client):
        headers = await register_and_login(
            app_client, "webhook-notaurl@example.com", "a-strong-password-1"
        )
        resp = await app_client.patch(
            "/api/auth/me/webhook",
            json={"webhook_url": "not a url"},
            headers=headers,
        )
        assert resp.status_code == 422


class TestWebhookFiresOnApprove:
    async def test_webhook_posts_on_approve(self, app_client, httpx_mock):
        user_headers = await register_and_login(
            app_client, "webhook-approve@example.com", "a-strong-password-1"
        )
        await app_client.patch(
            "/api/auth/me/webhook",
            json={"webhook_url": "https://example.com/cadence-hook"},
            headers=user_headers,
        )
        created = await app_client.post(
            "/api/requests", json=_payload(), headers=user_headers
        )
        request_id = created.json()["id"]

        httpx_mock.add_response(
            url="https://example.com/cadence-hook", method="POST", json={"ok": True}
        )

        admin_headers = await make_admin_headers(app_client)
        resp = await app_client.post(
            f"/api/requests/{request_id}/approve",
            json={"note": "go ahead"},
            headers=admin_headers,
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

        sent = httpx_mock.get_requests()
        assert len(sent) == 1
        body = json.loads(sent[0].content)
        assert body["event"] == "approved"
        assert body["status"] == "approved"
        assert body["request_id"] == request_id
        assert body["title"] == "Tum Hi Ho"
        assert body["artist"] == "Arijit Singh"

    async def test_no_webhook_call_when_url_unset(self, app_client, httpx_mock):
        user_headers = await register_and_login(
            app_client, "webhook-unset@example.com", "a-strong-password-1"
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
        assert httpx_mock.get_requests() == []


class TestWebhookDoesNotFireOnReject:
    async def test_no_webhook_call_on_reject(self, app_client, httpx_mock):
        user_headers = await register_and_login(
            app_client, "webhook-reject@example.com", "a-strong-password-1"
        )
        await app_client.patch(
            "/api/auth/me/webhook",
            json={"webhook_url": "https://example.com/cadence-hook"},
            headers=user_headers,
        )
        created = await app_client.post(
            "/api/requests", json=_payload(), headers=user_headers
        )
        request_id = created.json()["id"]

        # Deliberately no httpx_mock.add_response registered for the webhook URL: if reject
        # ever tried to call it, the unmatched request would blow up this test.
        admin_headers = await make_admin_headers(app_client)
        resp = await app_client.post(
            f"/api/requests/{request_id}/reject",
            json={"note": "no thanks"},
            headers=admin_headers,
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        assert httpx_mock.get_requests() == []


class TestWebhookSsrfGuardAtDeliveryTime:
    """The webhook_url column and the PATCH endpoint only validate URL *format* (http/https
    with a host) -- they do not, and must not need to, resolve DNS or block private ranges at
    save time. The actual SSRF defense lives in app.core.safe_http's POLICY_PUBLIC_ONLY policy,
    exercised here at approve time. A private-IP webhook_url must be accepted at save time
    (it is syntactically a valid http URL) and then must never actually be reached over the
    network when a request of that user's is approved -- and the approval itself must still
    succeed regardless."""

    async def test_private_ip_webhook_is_saved_but_never_actually_called(
        self, app_client, httpx_mock
    ):
        user_headers = await register_and_login(
            app_client, "webhook-ssrf1@example.com", "a-strong-password-1"
        )
        save_resp = await app_client.patch(
            "/api/auth/me/webhook",
            json={"webhook_url": "http://127.0.0.1:9696/internal-hook"},
            headers=user_headers,
        )
        # Format-valid, so save-time succeeds -- the guard is enforced at delivery, not here.
        assert save_resp.status_code == 200

        created = await app_client.post(
            "/api/requests", json=_payload(), headers=user_headers
        )
        request_id = created.json()["id"]

        # No response registered for the loopback URL: if the SSRF guard were bypassed and a
        # real request reached the mocked transport, pytest-httpx would raise for the
        # unmatched request and this test would fail loudly.
        admin_headers = await make_admin_headers(app_client)
        resp = await app_client.post(
            f"/api/requests/{request_id}/approve",
            json={"note": "go ahead"},
            headers=admin_headers,
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"
        assert httpx_mock.get_requests() == []

    async def test_metadata_ip_webhook_never_actually_called(
        self, app_client, httpx_mock
    ):
        user_headers = await register_and_login(
            app_client, "webhook-ssrf2@example.com", "a-strong-password-1"
        )
        await app_client.patch(
            "/api/auth/me/webhook",
            json={"webhook_url": "http://169.254.169.254/latest/meta-data/"},
            headers=user_headers,
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
        assert httpx_mock.get_requests() == []


class TestWebhookFailureDoesNotAffectRequestStatus:
    async def test_webhook_timeout_still_leaves_request_approved(
        self, app_client, httpx_mock
    ):
        user_headers = await register_and_login(
            app_client, "webhook-timeout@example.com", "a-strong-password-1"
        )
        await app_client.patch(
            "/api/auth/me/webhook",
            json={"webhook_url": "https://example.com/cadence-hook"},
            headers=user_headers,
        )
        created = await app_client.post(
            "/api/requests", json=_payload(), headers=user_headers
        )
        request_id = created.json()["id"]

        httpx_mock.add_exception(httpx.TimeoutException("timed out"))

        admin_headers = await make_admin_headers(app_client)
        resp = await app_client.post(
            f"/api/requests/{request_id}/approve",
            json={"note": "go ahead"},
            headers=admin_headers,
        )

        # The approve call itself must succeed and report approved, even though the webhook
        # delivery attempt raised inside notify_webhook.
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

        mine = await app_client.get("/api/requests/mine", headers=user_headers)
        matching = [r for r in mine.json() if r["id"] == request_id]
        assert matching[0]["status"] == "approved"

    async def test_webhook_server_error_still_leaves_request_approved(
        self, app_client, httpx_mock
    ):
        user_headers = await register_and_login(
            app_client, "webhook-500@example.com", "a-strong-password-1"
        )
        await app_client.patch(
            "/api/auth/me/webhook",
            json={"webhook_url": "https://example.com/cadence-hook"},
            headers=user_headers,
        )
        created = await app_client.post(
            "/api/requests", json=_payload(), headers=user_headers
        )
        request_id = created.json()["id"]

        httpx_mock.add_response(
            url="https://example.com/cadence-hook", method="POST", status_code=500
        )

        admin_headers = await make_admin_headers(app_client)
        resp = await app_client.post(
            f"/api/requests/{request_id}/approve",
            json={"note": "go ahead"},
            headers=admin_headers,
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"
