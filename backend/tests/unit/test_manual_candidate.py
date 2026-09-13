"""Unit tests for resolving a hand-picked candidate out of a request's own audit trail.

The security-relevant half of the override feature lives here: every one of these tests is
about what the resolver REFUSES. A candidate that resolves is only ever one Cadence itself
recorded seeing, so a crafted pointer can neither introduce a download target nor read another
request's history.
"""

from __future__ import annotations

import pytest

from app.models.request import FulfillmentAttempt, Request, RequestStatus
from app.services.manual_candidate import (
    CandidateNotActionableError,
    CandidateNotFoundError,
    CandidateQualityRefusedError,
    RequestNotPickableError,
    resolve_manual_candidate,
)


def _request(**overrides) -> Request:
    fields = {
        "id": "req-1",
        "user_id": "user-1",
        "title": "Labbar Bomma",
        "artist": "Sooraj Santhosh",
        "source": "apple",
        "source_id": "track-1",
        "dedupe_key": "apple:track-1",
        "status": RequestStatus.FAILED,
        "desired_quality": "any",
        "lossy_accepted": False,
    }
    fields.update(overrides)
    return Request(**fields)


def _attempt(
    candidates: list | None, *, attempt_id: str = "att-1", request_id: str = "req-1"
):
    return FulfillmentAttempt(
        id=attempt_id,
        request_id=request_id,
        backend="ytdlp",
        query_used="Sooraj Santhosh Labbar Bomma",
        candidates_considered=candidates,
        chosen_candidate=None,
        outcome="download_failed",
    )


def _candidate(**overrides) -> dict:
    body = {
        "backend": "ytdlp",
        "identifier": "dQw4w9WgXcQ",
        "name": "Sooraj Santhosh, Ranina Reddy - Labbar Bomma",
        "similarity": 0.512,
        "quality": "lossy",
        "extra": {"uploader": "Aditya Music"},
    }
    body.update(overrides)
    return body


class TestResolvingAValidPick:
    def test_returns_the_candidate_recorded_in_the_audit_trail(self):
        request_row = _request()
        attempt = _attempt([_candidate(identifier="wrong-one"), _candidate()])

        resolved = resolve_manual_candidate(
            request_row, [attempt], attempt_id="att-1", candidate_index=1
        )

        # Every field comes from the stored row, which is the whole point: nothing the caller
        # sent can appear here, because the caller sent only two integers' worth of pointer.
        assert resolved["backend"] == "ytdlp"
        assert resolved["identifier"] == "dQw4w9WgXcQ"
        assert resolved["name"] == "Sooraj Santhosh, Ranina Reddy - Labbar Bomma"
        assert resolved["extra"] == {"uploader": "Aditya Music"}

    @pytest.mark.parametrize(
        "status",
        [
            RequestStatus.FAILED,
            RequestStatus.AWAITING_QUALITY,
            RequestStatus.PENDING,
            RequestStatus.APPROVED,
        ],
    )
    def test_allowed_in_every_status_where_nothing_is_in_flight_or_delivered(
        self, status
    ):
        request_row = _request(status=status)
        resolved = resolve_manual_candidate(
            _request(status=status) and request_row,
            [_attempt([_candidate()])],
            attempt_id="att-1",
            candidate_index=0,
        )
        assert resolved["identifier"] == "dQw4w9WgXcQ"

    def test_falls_back_to_the_attempts_own_backend_for_older_rows(self):
        """A row predating per-candidate `backend` still names its tier via the attempt."""
        request_row = _request()
        attempt = _attempt([_candidate(backend=None)])

        resolved = resolve_manual_candidate(
            request_row, [attempt], attempt_id="att-1", candidate_index=0
        )

        assert resolved["backend"] == "ytdlp"


class TestStatusGuard:
    @pytest.mark.parametrize(
        "status",
        [RequestStatus.FULFILLED, RequestStatus.DOWNLOADING, RequestStatus.REJECTED],
    )
    def test_refuses_statuses_where_a_re_run_would_do_damage(self, status):
        """Fulfilled would overwrite a library file, downloading races the worker, and
        rejected is an admin call a requester must not route around."""
        request_row = _request(status=status)

        with pytest.raises(RequestNotPickableError) as exc:
            resolve_manual_candidate(
                request_row,
                [_attempt([_candidate()])],
                attempt_id="att-1",
                candidate_index=0,
            )

        # The offending status is carried, so the route can name it back to the caller.
        assert exc.value.args[0] == status.value


class TestRejectingBadPointers:
    def test_unknown_attempt_id_is_refused(self):
        with pytest.raises(CandidateNotFoundError, match="not part of this request"):
            resolve_manual_candidate(
                _request(),
                [_attempt([_candidate()])],
                attempt_id="nope",
                candidate_index=0,
            )

    def test_an_attempt_belonging_to_another_request_is_refused(self):
        """Otherwise one request's endpoint could be used to reach another's audit trail."""
        foreign = _attempt(
            [_candidate()], attempt_id="att-1", request_id="someone-elses"
        )

        with pytest.raises(CandidateNotFoundError, match="not part of this request"):
            resolve_manual_candidate(
                _request(), [foreign], attempt_id="att-1", candidate_index=0
            )

    @pytest.mark.parametrize("index", [1, 99, -1])
    def test_out_of_range_index_is_refused_with_the_real_count(self, index):
        with pytest.raises(CandidateNotFoundError, match="recorded 1 candidate"):
            resolve_manual_candidate(
                _request(),
                [_attempt([_candidate()])],
                attempt_id="att-1",
                candidate_index=index,
            )

    def test_an_attempt_with_no_candidates_is_refused(self):
        with pytest.raises(CandidateNotFoundError, match="recorded 0 candidate"):
            resolve_manual_candidate(
                _request(), [_attempt(None)], attempt_id="att-1", candidate_index=0
            )

    def test_a_non_dict_candidate_row_is_refused(self):
        with pytest.raises(CandidateNotActionableError, match="not recorded in a form"):
            resolve_manual_candidate(
                _request(),
                [_attempt(["just a string"])],
                attempt_id="att-1",
                candidate_index=0,
            )


class TestRejectingUnactionableCandidates:
    def test_a_candidate_with_no_identifier_is_refused_and_says_why(self):
        """Every row written before candidates carried an identifier lands here."""
        attempt = _attempt([{"name": "Some Release [FLAC]", "similarity": 0.9}])

        with pytest.raises(CandidateNotActionableError) as exc:
            resolve_manual_candidate(
                _request(), [attempt], attempt_id="att-1", candidate_index=0
            )

        # Actionable, not generic: it says what to do instead of just refusing.
        assert "Retry the request to search again" in str(exc.value)

    @pytest.mark.parametrize("identifier", ["", "   ", 12345, None])
    def test_a_blank_or_non_string_identifier_is_refused(self, identifier):
        attempt = _attempt([_candidate(identifier=identifier)])

        with pytest.raises(CandidateNotActionableError):
            resolve_manual_candidate(
                _request(), [attempt], attempt_id="att-1", candidate_index=0
            )

    def test_an_unknown_backend_is_refused_rather_than_dispatched(self):
        """There is no tier that could act on it, so naming one Cadence does not run is refused."""
        attempt = _attempt([_candidate(backend="evil-indexer")])

        with pytest.raises(CandidateNotActionableError, match="not a download source"):
            resolve_manual_candidate(
                _request(), [attempt], attempt_id="att-1", candidate_index=0
            )

    def test_a_candidate_with_no_name_is_refused(self):
        attempt = _attempt([_candidate(name="")])

        with pytest.raises(CandidateNotActionableError, match="no name recorded"):
            resolve_manual_candidate(
                _request(), [attempt], attempt_id="att-1", candidate_index=0
            )


class TestLosslessContract:
    @pytest.mark.parametrize("quality", ["lossy", "unknown", None])
    def test_a_non_lossless_pick_against_a_lossless_ask_is_refused(self, quality):
        """The exact silent downgrade the awaiting_quality flow exists to prevent."""
        request_row = _request(desired_quality="lossless", lossy_accepted=False)
        attempt = _attempt([_candidate(quality=quality)])

        with pytest.raises(CandidateQualityRefusedError) as exc:
            resolve_manual_candidate(
                request_row, [attempt], attempt_id="att-1", candidate_index=0
            )

        # Refused with the way forward named, not just refused.
        assert "Accept a lossy copy" in str(exc.value)

    def test_a_lossless_pick_against_a_lossless_ask_is_allowed(self):
        request_row = _request(desired_quality="lossless")
        attempt = _attempt([_candidate(quality="lossless", backend="prowlarr")])

        resolved = resolve_manual_candidate(
            request_row, [attempt], attempt_id="att-1", candidate_index=0
        )

        assert resolved["quality"] == "lossless"

    def test_a_lossy_pick_is_allowed_once_the_requester_has_settled(self):
        """lossy_accepted is the explicit consent, so the hard filter stops applying."""
        request_row = _request(desired_quality="lossless", lossy_accepted=True)
        attempt = _attempt([_candidate(quality="lossy")])

        resolved = resolve_manual_candidate(
            request_row, [attempt], attempt_id="att-1", candidate_index=0
        )

        assert resolved["quality"] == "lossy"

    def test_a_lossy_pick_is_fine_for_a_request_that_never_asked_for_lossless(self):
        request_row = _request(desired_quality="any")
        attempt = _attempt([_candidate(quality="lossy")])

        resolved = resolve_manual_candidate(
            request_row, [attempt], attempt_id="att-1", candidate_index=0
        )

        assert resolved["identifier"] == "dQw4w9WgXcQ"
