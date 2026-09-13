"""Which fulfillment tier gets credited with delivering a track.

Derived from the attempts audit trail rather than a column on the request, so these tests pin
the derivation rule itself: the LAST DOWNLOAD_COMPLETED row wins. That distinction is the
whole point -- a request Prowlarr failed and Soulseek rescued must name Soulseek, and reading
the first completion (or the first attempt of any kind) would credit the wrong tier.
"""

from __future__ import annotations

from app.models.request import FulfillmentAttempt, FulfillmentOutcome, Request
from app.services.requests import delivered_by_backend


def _request_with(*attempts: tuple[str, FulfillmentOutcome]) -> Request:
    """A Request carrying an already-populated attempts trail, in the order given.

    Built in memory rather than through the database: `delivered_by_backend` reads the loaded
    relationship, and the ordering it depends on is the order the worker appended rows in.
    """
    request_row = Request(
        user_id="user-1",
        title="Tum Hi Ho",
        artist="Arijit Singh",
        source="apple",
        source_id="track-1",
        dedupe_key="apple:track-1",
    )
    request_row.attempts = [
        FulfillmentAttempt(
            request_id="req-1",
            backend=backend,
            query_used="Arijit Singh Tum Hi Ho",
            candidates_considered=None,
            chosen_candidate=None,
            outcome=outcome.value,
        )
        for backend, outcome in attempts
    ]
    return request_row


class TestDeliveredByBackend:
    def test_nothing_delivered_yet_reports_none(self):
        request_row = _request_with(
            ("prowlarr", FulfillmentOutcome.SEARCHED_NO_CANDIDATES),
            ("slskd", FulfillmentOutcome.DOWNLOAD_STARTED),
        )
        assert delivered_by_backend(request_row) is None

    def test_a_request_with_no_attempts_at_all_reports_none(self):
        assert delivered_by_backend(_request_with()) is None

    def test_credits_the_tier_that_completed_the_download(self):
        request_row = _request_with(
            ("prowlarr", FulfillmentOutcome.GRABBED),
            ("prowlarr", FulfillmentOutcome.DOWNLOAD_COMPLETED),
        )
        assert delivered_by_backend(request_row) == "prowlarr"

    def test_credits_the_later_tier_that_rescued_an_earlier_failure(self):
        """The regression this guards: crediting the first tier tried, or the first completion.

        Prowlarr searched and found nothing, Soulseek then delivered. Naming "prowlarr" here
        would tell the requester their track came from a torrent it never came from.
        """
        request_row = _request_with(
            ("prowlarr", FulfillmentOutcome.SEARCHED_NO_CANDIDATES),
            ("slskd", FulfillmentOutcome.DOWNLOAD_STARTED),
            ("slskd", FulfillmentOutcome.DOWNLOAD_COMPLETED),
        )
        assert delivered_by_backend(request_row) == "slskd"

    def test_credits_the_last_tier_when_an_earlier_one_also_completed(self):
        """A retried request appends to the same trail, so more than one completion can exist.

        The file on disk is the one the most recent completion produced, so that is the tier
        the badge has to name.
        """
        request_row = _request_with(
            ("prowlarr", FulfillmentOutcome.DOWNLOAD_COMPLETED),
            ("slskd", FulfillmentOutcome.DOWNLOAD_COMPLETED),
        )
        assert delivered_by_backend(request_row) == "slskd"

    def test_the_youtube_tier_is_credited_like_any_other(self):
        request_row = _request_with(
            ("prowlarr", FulfillmentOutcome.SEARCHED_NO_CANDIDATES),
            ("slskd", FulfillmentOutcome.SEARCHED_NO_QUALIFYING_CANDIDATE),
            ("ytdlp", FulfillmentOutcome.DOWNLOAD_COMPLETED),
        )
        assert delivered_by_backend(request_row) == "ytdlp"

    def test_a_later_failure_does_not_erase_the_delivery(self):
        """A tier that failed *after* one delivered must not blank the badge.

        Only DOWNLOAD_COMPLETED rows are considered at all, so trailing noise in the audit
        trail cannot change who delivered the file.
        """
        request_row = _request_with(
            ("slskd", FulfillmentOutcome.DOWNLOAD_COMPLETED),
            ("ytdlp", FulfillmentOutcome.DOWNLOAD_FAILED),
        )
        assert delivered_by_backend(request_row) == "slskd"
