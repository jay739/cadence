"""Rate limiting setup (slowapi/limits), applied to auth and search endpoints per the mission.

Keyed by client IP by default. In production behind the operator's reverse proxy, this relies
on X-Forwarded-For being set correctly by the proxy; slowapi's get_remote_address handles the
common cases. Limits are intentionally modest: generous enough for normal interactive use,
tight enough to blunt credential-stuffing and search-endpoint abuse (which also fans out to
three external APIs per call).
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=[])

AUTH_RATE_LIMIT = "10/minute"
SEARCH_RATE_LIMIT = "30/minute"
# The streaming variant gets double the batch budget because search-as-you-type legitimately
# fires several debounced requests per query a user types (each abandoned one cancels its
# in-flight source tasks server-side, so the real upstream cost per abandoned stream is low).
# 60/minute still respects MusicBrainz's own 1-req/sec ceiling as the effective upper bound.
SEARCH_STREAM_RATE_LIMIT = "60/minute"
REQUEST_CREATE_RATE_LIMIT = "20/minute"
# Spotify personalization: connect/disconnect are state-changing and infrequent in normal use
# (same budget as auth); the library-browse routes each make a real outbound Spotify call
# (and, in the worst case, a token refresh), so they get the same budget as /api/search.
SPOTIFY_CONNECTION_RATE_LIMIT = "10/minute"
SPOTIFY_LIBRARY_RATE_LIMIT = "30/minute"
