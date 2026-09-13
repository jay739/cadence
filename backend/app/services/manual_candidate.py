"""Resolving and validating a candidate the requester picked by hand.

Scoring will always sometimes pick the wrong thing. A request for "Sooraj Santhosh & Ranina
Reddy Labbar Bomma" reached the YouTube tier, scored ten candidates, picked one at similarity
0.685, started downloading it and failed; the requester could see it had chosen wrong and had
no way to say "no, take that one instead". Better scoring does not fix that class of problem,
so the fix is an override, and this module is the part of it that decides whether a given
override is allowed to happen at all.

The central rule here: **the client sends a pointer, never a candidate.** A browser posts
"attempt X, candidate index N", and this module reads the candidate out of that attempt's own
`candidates_considered` list -- a list Cadence itself wrote during a real fulfillment pass.
Nothing the client typed is stored, and nothing the client typed is ever handed to an outbound
call. A crafted payload cannot introduce a Prowlarr guid, a Soulseek peer, or a YouTube video
id that Cadence did not already see and record for this exact request.
"""

from __future__ import annotations

from app.models.request import FulfillmentAttempt, Request, RequestStatus
from app.services.scoring import QUALITY_LOSSLESS

# Which tiers a manual pick can name. A candidate row carrying anything else is refused rather
# than dispatched: fulfillment has exactly these three tiers, and an unrecognised backend has
# no code path that could act on its identifier.
KNOWN_BACKENDS = ("prowlarr", "slskd", "ytdlp")

# Statuses a manual pick may be applied to, and why each is or is not here.
#
# FAILED and AWAITING_QUALITY are the two states this feature exists for: fulfillment has
# stopped, nothing is in flight, and the requester is looking at an audit trail that shows a
# wrong pick. PENDING and APPROVED are allowed because nothing has been downloaded yet either,
# so pinning a candidate before the worker reaches the request is harmless and useful.
#
# Deliberately absent:
#   * FULFILLED -- a file is already sitting in the library at final_file_path, and re-running
#     fulfillment would overwrite it (_finalize_file writes to the rendered library path, which
#     is a pure function of artist/title/album/ext, so a second pass lands on the same name).
#     Replacing a track the requester already has, on one click, is not something to do
#     silently; they can re-request it if they genuinely want a different copy.
#   * DOWNLOADING -- a transfer is in flight and the worker holds this row. Re-queueing
#     underneath it risks two tiers writing the same destination path.
#   * REJECTED -- an admin decision. A requester must not route around it by picking a
#     candidate; re-requesting (which clears the rejection) is the existing, visible path.
PICKABLE_STATUSES = (
    RequestStatus.FAILED,
    RequestStatus.AWAITING_QUALITY,
    RequestStatus.PENDING,
    RequestStatus.APPROVED,
)


class ManualCandidateError(Exception):
    """Base for every refusal to accept a hand-picked candidate.

    A closed hierarchy per domain rather than bare exception handling at the call site: the
    route maps each subclass to its own status code and its own specific message, so a caller
    is told which of these actually happened instead of a generic failure.
    """


class RequestNotPickableError(ManualCandidateError):
    """The request is in a status where overriding the pick is not allowed.

    Carries the offending status value, so the route can name it back to the caller the same
    way retry and accept-lossy already name theirs.
    """


class CandidateNotFoundError(ManualCandidateError):
    """The pointer does not resolve: no such attempt on this request, or no such index in it.

    Deliberately also raised when the attempt belongs to a *different* request, so a pointer
    cannot be used to read one request's audit trail through another's endpoint.
    """


class CandidateNotActionableError(ManualCandidateError):
    """The candidate resolved, but is not something a tier could actually be run against.

    A row from before candidates carried an identifier, or one whose backend is not a tier
    that exists. Distinct from CandidateNotFoundError because the fix differs: this one is
    "that pick cannot be acted on", not "that pick does not exist".
    """


class CandidateQualityRefusedError(ManualCandidateError):
    """Picking this candidate by hand would silently downgrade a lossless request.

    The same contract `qualifying_candidates_ranked(require_lossless=True)` enforces on the
    automatic path, enforced again here rather than assumed: a hand-picked lossy file against
    an unanswered lossless ask is exactly the silent downgrade the AWAITING_QUALITY flow
    exists to prevent. The requester is pointed at that flow instead of being quietly given
    what they asked Cadence not to accept.
    """


def _requires_lossless(request_row: Request) -> bool:
    """Whether this request's lossless ask is still a hard filter.

    Same rule as the automatic path's own check in app.services.fulfillment: only "lossless"
    ever excludes anything, and only until the requester has explicitly agreed to settle.
    """
    return request_row.desired_quality == "lossless" and not request_row.lossy_accepted


def resolve_manual_candidate(
    request_row: Request,
    attempts: list[FulfillmentAttempt],
    *,
    attempt_id: str,
    candidate_index: int,
) -> dict:
    """Turn a client's pointer into a validated, storable candidate, or refuse it.

    Returns the dict to persist on `Request.manual_candidate`: backend, identifier, name,
    quality and extra, all copied from Cadence's own audit trail rather than from the caller.
    """
    if request_row.status not in PICKABLE_STATUSES:
        raise RequestNotPickableError(request_row.status.value)

    attempt = next(
        (a for a in attempts if a.id == attempt_id and a.request_id == request_row.id),
        None,
    )
    if attempt is None:
        raise CandidateNotFoundError(
            "That download attempt is not part of this request's history."
        )

    candidates = attempt.candidates_considered or []
    if candidate_index < 0 or candidate_index >= len(candidates):
        raise CandidateNotFoundError(
            f"That attempt recorded {len(candidates)} candidate(s), so there is no "
            f"candidate #{candidate_index + 1} to pick."
        )

    candidate = candidates[candidate_index]
    if not isinstance(candidate, dict):
        raise CandidateNotActionableError(
            "That candidate was not recorded in a form Cadence can act on. Retry the "
            "request to search again, then pick from the fresh results."
        )

    backend = candidate.get("backend") or attempt.backend
    identifier = candidate.get("identifier")
    name = candidate.get("name")

    if backend not in KNOWN_BACKENDS:
        raise CandidateNotActionableError(
            f"This candidate came from {backend!r}, which is not a download source Cadence "
            f"can run ({', '.join(KNOWN_BACKENDS)})."
        )
    if not isinstance(identifier, str) or not identifier.strip():
        # Every attempt row written before candidates carried an identifier lands here. There
        # is genuinely nothing to act on: the name alone does not tell Prowlarr which release,
        # slskd which peer's file, or the shim which video.
        raise CandidateNotActionableError(
            "This candidate was recorded before Cadence kept track of which exact release "
            "each result pointed at, so it can no longer be downloaded directly. Retry the "
            "request to search again, then pick from the fresh results."
        )
    if not isinstance(name, str) or not name.strip():
        raise CandidateNotActionableError(
            "That candidate has no name recorded, so Cadence cannot confirm which release "
            "it refers to."
        )

    quality = candidate.get("quality")
    if _requires_lossless(request_row) and quality != QUALITY_LOSSLESS:
        # Refused rather than downgraded, and refused with the way forward named. "unknown"
        # is refused alongside "lossy" for the reason qualifying_candidates_ranked gives:
        # the requester asked for a guarantee, and a release whose format was never
        # established is not one.
        raise CandidateQualityRefusedError(
            f"This request asked for a lossless copy, and that candidate is "
            f"{quality or 'of an unknown format'}. Downloading it would be the silent "
            f"downgrade the lossless option exists to prevent. Choose 'Accept a lossy copy' "
            f"first if a lossy file is genuinely fine, then pick it again."
        )

    extra = candidate.get("extra")
    return {
        "backend": backend,
        "identifier": identifier,
        "name": name,
        "quality": quality,
        # Carried through because the tiers need it to act: the Prowlarr path reads
        # `indexerId` off it to grab, and the slskd path reads `username` to know which peer
        # holds the file. Copied from the audit trail like everything else here, never from
        # the request body.
        "extra": extra if isinstance(extra, dict) else {},
    }
