"""Cover Art Archive fetch step: retrieves front-cover bytes for a MusicBrainz release.

Only ever called when a request carried a musicbrainz_recording_id and that lookup produced a
release MBID -- there is no title/artist guessing here, because embedding the wrong album's
artwork into someone's library is worse than embedding none.

Two things about the Cover Art Archive shape the code below:

- Every image URL it hands out (including the ones inside its own JSON index) is a redirect
  chain ending at archive.org's CDN, typically 307 -> 302 -> 200. app.core.safe_http sets
  follow_redirects=False on purpose, so redirects are followed manually here, one hop at a
  time, each hop re-validated by SafeHttpClient against POLICY_PUBLIC_ONLY. That is the whole
  point: an auto-following client would resolve only the first URL and then chase a Location
  header wherever it pointed, including back at a private address.
- Artwork is routinely several megabytes at full size. Cadence asks for the 500px thumbnail,
  which is what Navidrome serves to clients anyway, and still refuses anything over
  MAX_COVER_BYTES so a hostile or broken upstream cannot inflate every file in the library.
"""

from __future__ import annotations

import structlog

from app.core.safe_http import POLICY_PUBLIC_ONLY, SafeHttpClient

logger = structlog.get_logger(__name__)

COVER_ART_ARCHIVE_URL = "https://coverartarchive.org"
USER_AGENT = "Cadence/0.1.0 (self-hosted single-track music request service)"

# The 500px thumbnail: large enough to look right on a phone and a TV client, small enough that
# embedding it in every track does not bloat the library. Full-size scans are frequently 5-10MB.
COVER_SIZE = "500"

# Hard ceiling on what is embedded, independent of which size was requested. Checked against
# Content-Length first and against the downloaded body afterwards, since Content-Length is a
# claim by the server, not a guarantee.
MAX_COVER_BYTES = 2 * 1024 * 1024

# Redirect hops to follow before giving up. The observed chain is three responses
# (coverartarchive -> archive.org -> a CDN node); the allowance is deliberately small so a
# redirect loop terminates quickly rather than burning the fulfillment worker's time.
MAX_REDIRECT_HOPS = 5

_IMAGE_CONTENT_TYPES = ("image/jpeg", "image/png")


class CoverArt:
    """Fetched artwork: the raw bytes plus the MIME type mutagen needs to embed them."""

    __slots__ = ("data", "mime")

    def __init__(self, data: bytes, mime: str) -> None:
        self.data = data
        self.mime = mime


async def fetch_front_cover(
    release_id: str, client: SafeHttpClient | None = None
) -> CoverArt | None:
    """Fetch the front cover for a release MBID, or None if there isn't one to be had.

    Never raises: no artwork is a perfectly normal outcome (most releases in MusicBrainz have
    none at all), and a tagging step that fails the fulfillment over a missing image would be
    trading a delivered song for a cosmetic detail.
    """
    http = client or SafeHttpClient()
    url = f"{COVER_ART_ARCHIVE_URL}/release/{release_id}/front-{COVER_SIZE}"
    try:
        resp = await _get_following_redirects(http, url)
    except Exception as exc:  # noqa: BLE001 - artwork is best-effort polish
        logger.warning(
            "cover_art_fetch_failed",
            release_id=release_id,
            error=str(exc) or type(exc).__name__,
        )
        return None

    if resp is None:
        return None

    content_type = (
        (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    )
    if content_type not in _IMAGE_CONTENT_TYPES:
        logger.info(
            "cover_art_skipped_unexpected_content_type",
            release_id=release_id,
            content_type=content_type or "(none)",
        )
        return None

    data = resp.content
    if len(data) > MAX_COVER_BYTES:
        logger.info(
            "cover_art_skipped_too_large",
            release_id=release_id,
            bytes=len(data),
            limit=MAX_COVER_BYTES,
        )
        return None
    if not data:
        logger.info("cover_art_skipped_empty_body", release_id=release_id)
        return None

    return CoverArt(data=data, mime=content_type)


async def _get_following_redirects(http: SafeHttpClient, url: str):
    """GET url, following redirects manually so every hop is re-checked by the SSRF policy.

    Returns the first non-redirect response, or None if the chain 404s (no artwork for this
    release), exceeds MAX_REDIRECT_HOPS, or answers a redirect with no Location to follow.
    """
    for _ in range(MAX_REDIRECT_HOPS):
        resp = await http.get(
            url, policy=POLICY_PUBLIC_ONLY, headers={"User-Agent": USER_AGENT}
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location")
            if not location:
                return None
            url = location
            continue
        if resp.status_code != 200:
            # 404 is the overwhelmingly common case: the release simply has no cover art.
            return None
        # Content-Length is only a claim, but an early one: refusing here avoids pulling a
        # multi-megabyte body into memory just to discard it after the fact.
        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_COVER_BYTES:
            return None
        return resp
    return None
