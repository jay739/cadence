"""Resolve a pasted direct track link (Spotify or Apple Music) to one normalized TrackResult,
as an alternative to free-text search for a user who already knows exactly which recording
they want.

Reuses the same per-source adapters as the free-text /api/search flow: each of
SpotifySearchSource / AppleSearchSource now also exposes a `fetch_by_id()` alongside its
`_search()`, sharing the same response-normalization code, so the mapping into TrackResult
stays in exactly one place per source (see app/search_sources/apple.py and .../spotify.py).

Deliberately scoped to Spotify + Apple Music only. Both have a public per-track metadata
lookup addressable directly by the platform-native id already embedded in their web track
URLs. YouTube links are explicitly NOT supported here: the only YouTube-capable integration in
this repo (the ytdlp-shim service, app/services/ytdlp_shim.py) is download-only and exposes a
keyword-search endpoint, not a "resolve this exact video id's metadata without downloading it"
one. Building that from scratch is out of scope for this feature; see the PR description for
the deferred-platform note.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from app.schemas.search import TrackResult
from app.search_sources.apple import AppleSearchSource
from app.search_sources.spotify import SpotifySearchSource

PLATFORM_LABELS = {"spotify": "Spotify", "apple": "Apple Music"}

# Both platforms' ids are opaque alphanumeric tokens embedded directly in the URL; validating
# the shape here (rather than trusting whatever text follows the expected path segment) keeps
# a clearly-bogus id from ever reaching the outbound call, alongside safe_http.py's own
# allowlisted-host/query-param-only defenses.
_SPOTIFY_ID_MAX_LEN = 64
_APPLE_ID_MAX_LEN = 15  # Apple/iTunes numeric ids comfortably fit a signed 64-bit int


class UnrecognizedTrackLinkError(Exception):
    """The pasted URL isn't a Spotify or Apple Music *track* link this module knows how to
    parse: wrong host, malformed URL, or a recognized-platform link that points at something
    other than a single track (an artist, playlist, or bare album page).
    """


class TrackLinkNotFoundError(Exception):
    """The URL parsed to a valid platform + native id, but that platform's own API reports no
    such track (deleted, region-locked upstream, or the id was simply mistyped).
    """

    def __init__(self, platform: str) -> None:
        self.platform = platform
        super().__init__(f"Couldn't find that track on {PLATFORM_LABELS[platform]}")


class TrackLinkSourceNotConfiguredError(Exception):
    """The URL is a valid Spotify track link, but the operator hasn't configured Spotify API
    credentials server-side. Apple/iTunes lookups never raise this: that API is keyless.
    """

    def __init__(self, platform: str) -> None:
        self.platform = platform
        super().__init__(
            f"{PLATFORM_LABELS[platform]} lookups aren't configured on this server yet"
        )


def _extract_spotify_track_id(path: str) -> str | None:
    # Real Spotify track links vary the segment(s) before "track" (a locale prefix like
    # "intl-en", an "embed" prefix, or nothing at all) but "track" is always immediately
    # followed by the id, so search for that segment rather than anchoring a rigid full-path
    # regex to one specific prefix shape.
    segments = [s for s in path.split("/") if s]
    if "track" not in segments:
        return None
    idx = segments.index("track")
    if idx + 1 >= len(segments):
        return None
    candidate = segments[idx + 1]
    if candidate.isalnum() and len(candidate) <= _SPOTIFY_ID_MAX_LEN:
        return candidate
    return None


def _extract_apple_track_id(path: str, query: str) -> str | None:
    segments = [s for s in path.split("/") if s]
    if "song" in segments:
        # .../song/<slug>/<id>: the id is the segment right after the slug that follows "song".
        idx = segments.index("song")
        if idx + 2 < len(segments):
            candidate = segments[idx + 2]
            if candidate.isdigit() and len(candidate) <= _APPLE_ID_MAX_LEN:
                return candidate
        return None
    if "album" in segments:
        # .../album/<slug>/<album-id>?i=<track-id>: an album link only names one exact track
        # when it carries this query param; without it, it's ambiguous (the whole album).
        track_params = parse_qs(query).get("i")
        track_param = track_params[0] if track_params else None
        if (
            track_param
            and track_param.isdigit()
            and len(track_param) <= _APPLE_ID_MAX_LEN
        ):
            return track_param
        return None
    return None


def parse_track_link(url: str) -> tuple[str, str]:
    """Returns (platform, native_id) for a recognized Spotify or Apple Music track link.

    Raises UnrecognizedTrackLinkError for anything else: wrong host, malformed URL, or a
    recognized platform whose link doesn't identify one specific track.
    """
    generic_error = "That doesn't look like a Spotify or Apple Music track link"
    try:
        parsed = urlparse(url.strip())
    except ValueError as exc:
        raise UnrecognizedTrackLinkError(generic_error) from exc

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnrecognizedTrackLinkError(generic_error)

    host = parsed.hostname.lower()

    if host == "open.spotify.com":
        track_id = _extract_spotify_track_id(parsed.path)
        if track_id is None:
            raise UnrecognizedTrackLinkError(
                "That Spotify link doesn't look like a track link"
            )
        return "spotify", track_id

    if host == "music.apple.com":
        track_id = _extract_apple_track_id(parsed.path, parsed.query)
        if track_id is None:
            raise UnrecognizedTrackLinkError(
                "That Apple Music link doesn't look like a single-track link"
            )
        return "apple", track_id

    raise UnrecognizedTrackLinkError(generic_error)


async def fetch_track_by_id(
    platform: str,
    native_id: str,
    *,
    spotify_client_id: str | None,
    spotify_client_secret: str | None,
) -> TrackResult:
    """Dispatches to the matching adapter's fetch_by_id and returns one TrackResult.

    Raises TrackLinkSourceNotConfiguredError / TrackLinkNotFoundError for the route to
    translate into a specific 400/404. A transport/HTTP failure from the adapter propagates as
    SafeHttpError (uncaught here) for the route to translate into a 502, distinct from "not
    found" since the upstream platform itself, not the track, is the problem.
    """
    if platform == "spotify":
        source = SpotifySearchSource(spotify_client_id, spotify_client_secret)
        if not source.configured:
            raise TrackLinkSourceNotConfiguredError(platform)
        track = await source.fetch_by_id(native_id)
    else:
        track = await AppleSearchSource().fetch_by_id(native_id)

    if track is None:
        raise TrackLinkNotFoundError(platform)
    return track
