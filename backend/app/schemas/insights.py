"""Response models for the admin insight page (see app.services.insights)."""

from __future__ import annotations

from pydantic import BaseModel


class TierInsight(BaseModel):
    """How one fulfillment tier performed over the window."""

    backend: str
    # Audit rows this tier wrote. Always >= requests_tried, because a single successful pass
    # writes several (e.g. GRABBED then DOWNLOAD_COMPLETED).
    attempts: int
    # Distinct requests this tier actually ran against, ignoring passes where it was skipped
    # for not being configured.
    requests_tried: int
    # Distinct requests this tier carried all the way to a completed download.
    requests_delivered: int
    # requests_delivered / requests_tried, or null when the tier never ran at all -- which is
    # not the same as failing everything, and must not render as 0%.
    success_rate: float | None
    # Full outcome -> count breakdown, so the page can show *how* a tier spends its passes and
    # not just whether they worked.
    outcomes: dict[str, int]


class FailureInsight(BaseModel):
    """One reason passes ended without a file, and which tiers produced it."""

    outcome: str
    count: int
    backends: dict[str, int]


class DailyVolumeInsight(BaseModel):
    """Requests created on one day. Quiet days are present with a zero count, never omitted."""

    date: str
    requests: int


class TopArtistInsight(BaseModel):
    artist: str
    requests: int


class AdminInsightsOut(BaseModel):
    window_days: int
    # First day included in the window (YYYY-MM-DD, UTC), so the page can state the period it
    # is describing rather than just claiming "recently".
    since: str
    total_requests: int
    status_counts: dict[str, int]
    tiers: list[TierInsight]
    failures: list[FailureInsight]
    daily_volume: list[DailyVolumeInsight]
    top_artists: list[TopArtistInsight]
