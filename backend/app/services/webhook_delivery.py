"""Best-effort outbound notification to a user's configured webhook URL.

Fired after a request the user owns transitions to `approved`, `fulfilled`, or
`awaiting_quality` -- never for `rejected` or `failed` -- from three exact transition points:
app.services.requests.approve_request, app.services.fulfillment._finalize_file, and the
AWAITING_QUALITY branch of app.services.fulfillment.fulfill_request. That last one is the only
event of the three the user has to act on (nothing lossless was found and Cadence is waiting on
their answer), which is exactly why it is worth notifying rather than leaving to be discovered
on a later visit. This is explicitly scoped to a
single generic webhook URL for now; per-service integrations (Telegram, Discord, Pushbullet,
ntfy, ...) are a deliberate non-goal here and can build on top of this later.

The webhook URL is entirely user-supplied and Cadence makes the outbound call on the user's
behalf, which is a textbook SSRF vector (a user could point it at another Docker-network
service with no auth on its internal port, or at a cloud metadata endpoint). Every delivery is
therefore routed through app.core.safe_http's POLICY_PUBLIC_ONLY policy -- the same guard the
public search sources use -- never a raw httpx call. See app.core.safe_http for the full threat
model.

Delivery never raises and never affects its caller: any failure (blocked by the SSRF guard,
timeout, non-2xx, transport error, or anything else) is logged and swallowed here. Cadence's
own approve/fulfill transitions must never be delayed by, or fail because of, a user's
unreachable or hostile webhook endpoint. Callers are expected to invoke this only *after* the
relevant DB commit has already succeeded, never before or interleaved with it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import structlog

from app.core.safe_http import POLICY_PUBLIC_ONLY, SafeHttpClient, SafeHttpError
from app.models.request import Request
from app.models.user import User

logger = structlog.get_logger(__name__)

# Short timeout, no retries: a slow or dead third-party endpoint must never make the caller (an
# admin's approve click, or the fulfillment worker's single-job-at-a-time loop) wait around for
# it any longer than strictly necessary.
_client = SafeHttpClient(timeout_seconds=5.0, max_retries=0)

WebhookEvent = Literal["approved", "fulfilled", "awaiting_quality"]


async def notify_webhook(
    user: User,
    request_row: Request,
    event: WebhookEvent,
    *,
    client: SafeHttpClient | None = None,
) -> None:
    """POST a small status-change payload to `user.webhook_url`, if one is configured.

    No-op (not an error) when the user has no webhook_url set. Never raises: every failure mode
    is caught, logged via structlog, and swallowed -- see module docstring.
    """
    url = user.webhook_url
    if not url:
        return

    payload = {
        "event": event,
        "request_id": request_row.id,
        "title": request_row.title,
        "artist": request_row.artist,
        "status": request_row.status.value,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    http = client or _client
    try:
        await http.post(url, policy=POLICY_PUBLIC_ONLY, json=payload)
    except SafeHttpError as exc:
        # Covers both the SSRF guard rejecting the target outright (private/loopback/
        # link-local/metadata address, bad scheme, DNS failure) and an ordinary delivery
        # failure (timeout, transport error) -- safe_http raises the same exception type for
        # all of them, and none of them are actionable here beyond logging.
        # NB: the kwarg is `webhook_event`, not `event` -- structlog's bound logger methods
        # reserve `event` for the log line's own event name and raise a TypeError ("got
        # multiple values for argument 'event'") if a caller-supplied kwarg shadows it.
        logger.warning(
            "webhook_delivery_failed",
            user_id=user.id,
            request_id=request_row.id,
            webhook_event=event,
            error=str(exc),
        )
    except (
        Exception
    ) as exc:  # noqa: BLE001 - a notification must never break its caller
        logger.warning(
            "webhook_delivery_unexpected_error",
            user_id=user.id,
            request_id=request_row.id,
            webhook_event=event,
            error=str(exc),
        )
