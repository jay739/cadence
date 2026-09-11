"""Unit tests for app.services.webhook_delivery.notify_webhook.

The single most important property here: a user-supplied webhook URL pointing at a private,
loopback, or cloud-metadata address must never actually reach the network. These tests prove
that by NOT registering any httpx_mock response for the blocked cases and then asserting no
request was recorded at all -- pytest-httpx only records a request if one actually reached the
mocked transport, so an empty `httpx_mock.get_requests()` here means safe_http's
POLICY_PUBLIC_ONLY guard genuinely rejected the target before any socket was touched (the same
proof pattern app.core.safe_http's own tests use).
"""

from __future__ import annotations

import pytest

from app.models.request import Request, RequestStatus
from app.models.user import User, UserRole
from app.services.webhook_delivery import notify_webhook

pytestmark = pytest.mark.asyncio


def _user(webhook_url: str | None) -> User:
    return User(
        id="user-1",
        email="webhook-test@example.com",
        password_hash="x",
        role=UserRole.USER,
        webhook_url=webhook_url,
    )


def _request(status: RequestStatus = RequestStatus.APPROVED) -> Request:
    return Request(
        id="req-1",
        user_id="user-1",
        title="Tum Hi Ho",
        artist="Arijit Singh",
        album="Aashiqui 2",
        source="apple",
        source_id="track-1",
        dedupe_key="apple:track-1",
        status=status,
    )


class TestNoWebhookConfigured:
    async def test_no_op_when_webhook_url_unset(self, httpx_mock):
        user = _user(None)
        request_row = _request()

        await notify_webhook(user, request_row, "approved")

        assert httpx_mock.get_requests() == []


class TestSuccessfulDelivery:
    async def test_posts_expected_payload_to_configured_url(self, httpx_mock):
        user = _user("https://example.com/cadence-webhook")
        request_row = _request(status=RequestStatus.APPROVED)
        httpx_mock.add_response(
            url="https://example.com/cadence-webhook", method="POST", json={"ok": True}
        )

        await notify_webhook(user, request_row, "approved")

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        sent = requests[0]
        assert str(sent.url) == "https://example.com/cadence-webhook"
        import json

        body = json.loads(sent.content)
        assert body["event"] == "approved"
        assert body["request_id"] == "req-1"
        assert body["title"] == "Tum Hi Ho"
        assert body["artist"] == "Arijit Singh"
        assert body["status"] == "approved"
        assert "timestamp" in body

    async def test_fulfilled_event_reflects_fulfilled_status(self, httpx_mock):
        user = _user("https://example.com/cadence-webhook")
        request_row = _request(status=RequestStatus.FULFILLED)
        httpx_mock.add_response(
            url="https://example.com/cadence-webhook", method="POST"
        )

        await notify_webhook(user, request_row, "fulfilled")

        import json

        body = json.loads(httpx_mock.get_requests()[0].content)
        assert body["event"] == "fulfilled"
        assert body["status"] == "fulfilled"


class TestSsrfGuardIsGenuinelyExercised:
    """These must never register an httpx_mock response for the blocked URL: if the SSRF guard
    were somehow bypassed, the unmocked request would raise pytest-httpx's own "no response
    registered" error instead of silently succeeding, so a passing test here is real proof the
    guard fired before any outbound call was attempted."""

    async def test_loopback_url_never_reaches_transport_and_is_swallowed(
        self, httpx_mock
    ):
        user = _user("http://127.0.0.1:9696/hook")
        request_row = _request()

        await notify_webhook(user, request_row, "approved")  # must not raise

        assert httpx_mock.get_requests() == []

    async def test_cloud_metadata_url_never_reaches_transport_and_is_swallowed(
        self, httpx_mock
    ):
        user = _user("http://169.254.169.254/latest/meta-data/")
        request_row = _request()

        await notify_webhook(user, request_row, "approved")  # must not raise

        assert httpx_mock.get_requests() == []

    async def test_docker_internal_style_private_address_is_blocked(self, httpx_mock):
        # Mimics pointing the webhook at another service on the operator's own Docker
        # network (e.g. Prowlarr/qBittorrent/slskd), which typically has no auth on its
        # internal port.
        user = _user("http://172.18.0.5:8080/webhook")
        request_row = _request()

        await notify_webhook(user, request_row, "approved")  # must not raise

        assert httpx_mock.get_requests() == []


class TestDeliveryFailureIsSwallowed:
    async def test_transport_error_does_not_raise(self, httpx_mock):
        import httpx

        user = _user("https://example.com/cadence-webhook")
        request_row = _request()
        httpx_mock.add_exception(httpx.TimeoutException("timed out"))

        await notify_webhook(user, request_row, "approved")  # must not raise

    async def test_non_2xx_response_does_not_raise(self, httpx_mock):
        user = _user("https://example.com/cadence-webhook")
        request_row = _request()
        httpx_mock.add_response(
            url="https://example.com/cadence-webhook", method="POST", status_code=500
        )

        # notify_webhook doesn't call raise_for_status, but this documents/locks in that a
        # non-2xx response is not treated as fatal by the caller either way.
        await notify_webhook(user, request_row, "approved")
