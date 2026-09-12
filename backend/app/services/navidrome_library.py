"""Ask Navidrome whether a track is already in the library, before anyone requests it.

The point is to stop a user queueing a download of something they already own. That makes the
cost of the two possible mistakes wildly asymmetric: a missed hit costs a redundant download,
while a false hit tells someone "you already have this" about a track they do not have and
talks them out of requesting it. Everything below is tuned for the first mistake and against
the second -- titles must agree exactly once benign decorations are stripped, and artists must
agree exactly or by a whole-token subset of at least two tokens.

CREDENTIALS
-----------
This is the one Navidrome feature that needs a credential Cadence can actually hold. A user's
own Navidrome password is never stored (see app.services.navidrome_auth), so the library check
cannot run as the requesting user; it runs as an operator service account supplied via
NAVIDROME_ADMIN_USER / NAVIDROME_ADMIN_PASSWORD. Both are optional: with them unset the check
reports itself unavailable, the search UI simply shows no library badges, and nothing else
changes. Search must never depend on this working.

The wire protocol is shared with the auth backend via app.services.navidrome_subsonic, so there
is exactly one Subsonic implementation in the codebase.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import structlog

from app.core.safe_http import POLICY_ALLOW_PRIVATE, SafeHttpClient
from app.services.navidrome_subsonic import (
    SubsonicProtocolError,
    auth_params,
    error_code,
    is_ok,
    subsonic_body,
)

logger = structlog.get_logger(__name__)

# One search3 call per *distinct* title, bounded so a 50-track page cannot fan out into an
# unbounded burst against the operator's Navidrome. Concurrency is capped for the same reason;
# Navidrome is a local service and five in flight saturates it fine.
MAX_LOOKUPS = 25
MAX_CONCURRENCY = 5
SONGS_PER_LOOKUP = 25

# Short and retry-free on purpose: this call decorates the search UI, so a slow or flapping
# Navidrome must degrade to "no badge" quickly rather than hold up the page.
_TIMEOUT_SECONDS = 5.0

# Bracketed or dash-suffixed decorations that do not change *which recording* a title refers
# to, and so can be stripped before comparing. Anything that denotes a genuinely different
# recording -- live, acoustic, remix, radio edit, instrumental, demo, karaoke -- is deliberately
# absent: those must NOT collapse onto the studio version.
_BENIGN_DECORATIONS = frozenset(
    {
        "remaster",
        "remastered",
        "remaster version",
        "remastered version",
        "original mix",
        "original version",
        "album version",
        "single version",
        "explicit",
        "explicit version",
        "clean",
        "clean version",
        "bonus track",
        "bonus",
        "deluxe",
        "deluxe edition",
        "stereo",
        "mono",
        "expanded edition",
        "anniversary edition",
    }
)

_FEATURING_MARKERS = frozenset({"feat", "feats", "ft", "fts", "featuring"})

# Apostrophes are *deleted* rather than turned into a space, unlike every other punctuation
# mark: "Don't Stop" and "Dont Stop" are the same title typed two ways, and spacing them apart
# would split the word into "don t" and lose the match. Curly and ASCII forms both, since
# tagging tools disagree about which to write.
_APOSTROPHES = "'’ʼ`´"
_BRACKET_RE = re.compile(r"[(\[{]([^()\[\]{}]*)[)\]}]")
_DASH_SPLIT_RE = re.compile(r"\s+[-–—]\s+")
_YEAR_RE = re.compile(r"\b\d{4}\b")


@dataclass(frozen=True)
class LibraryTrackQuery:
    """One track to look up. `key` is opaque to this module and echoed back on the result."""

    key: str
    title: str
    artist: str


@dataclass(frozen=True)
class LibraryHit:
    navidrome_id: str
    title: str
    artist: str
    album: str | None = None


@dataclass(frozen=True)
class LibraryCheckResult:
    """`available` says whether Navidrome actually answered, which is not the same as "no hits".

    When it is False the caller must present *nothing* rather than "not in your library": an
    unreachable or unconfigured Navidrome means unknown, and claiming a track is absent would be
    the same category of lie as claiming it is present.
    """

    available: bool
    hits: dict[str, LibraryHit] = field(default_factory=dict)


# --- text canonicalisation -------------------------------------------------------------


def _fold(value: str) -> str:
    """Casefold, strip diacritics, reduce punctuation to spaces, collapse whitespace.

    Deliberately lossy and symmetric: it is only ever used to compare two strings with each
    other, never to display or store anything.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = without_marks.casefold()
    cleaned = "".join(
        "" if ch in _APOSTROPHES else (ch if ch.isalnum() else " ") for ch in lowered
    )
    return " ".join(cleaned.split())


def _is_featuring(folded: str) -> bool:
    tokens = folded.split()
    return bool(tokens) and tokens[0] in _FEATURING_MARKERS


def _droppable(fragment: str) -> bool:
    """Whether a bracketed group or dash-tail can be removed without changing the recording."""
    folded = _fold(fragment)
    if not folded:
        return True
    if _is_featuring(folded):
        return True
    # "Remastered 2011" and "2011 Remaster" are the same decoration with a year bolted on.
    without_year = " ".join(_YEAR_RE.sub(" ", folded).split())
    return without_year in _BENIGN_DECORATIONS


def _drop_benign_brackets(value: str) -> str:
    previous = None
    while previous != value:
        previous = value
        value = _BRACKET_RE.sub(
            lambda m: "" if _droppable(m.group(1)) else m.group(0), value
        )
    return value


def _drop_benign_dash_tail(value: str) -> str:
    parts = _DASH_SPLIT_RE.split(value)
    while len(parts) > 1 and _droppable(parts[-1]):
        parts.pop()
    return " - ".join(parts)


def canonical_title(value: str) -> str:
    """A title reduced to the recording it names, with benign decoration removed."""
    stripped = _drop_benign_dash_tail(_drop_benign_brackets(value))
    folded = _fold(stripped)
    # A title made up entirely of what we treat as decoration ("Remastered") is not decoration,
    # it is the title. Fall back rather than reducing it to the empty string.
    return folded or _fold(value)


def _cut_featuring(folded: str) -> str:
    tokens = folded.split()
    for index, token in enumerate(tokens):
        if index > 0 and token in _FEATURING_MARKERS:
            return " ".join(tokens[:index])
    return folded


def _drop_leading_the(folded: str) -> str:
    return folded[4:] if folded.startswith("the ") else folded


def canonical_artist(value: str) -> str:
    """An artist credit reduced to the primary act, without featured guests."""
    folded = _fold(_drop_benign_brackets(value))
    return _drop_leading_the(_cut_featuring(folded))


def titles_match(left: str, right: str) -> bool:
    """Exact agreement after canonicalisation. No fuzzy distance, by design."""
    canonical = canonical_title(left)
    return bool(canonical) and canonical == canonical_title(right)


def artists_match(left: str, right: str) -> bool:
    """Exact agreement, or a whole-token subset of at least two tokens.

    The two-token floor is what keeps this conservative. It admits "Simon & Garfunkel" against
    "Simon and Garfunkel", and "The Beatles" against "Beatles" (the leading article is dropped
    before comparing), while rejecting "Blue" against "Blue October" -- a single shared token is
    far too weak to assert that someone already owns a track.
    """
    left_canonical = canonical_artist(left)
    right_canonical = canonical_artist(right)
    if not left_canonical or not right_canonical:
        return False
    if left_canonical == right_canonical:
        return True

    left_tokens = set(left_canonical.split())
    right_tokens = set(right_canonical.split())
    smaller, larger = (
        (left_tokens, right_tokens)
        if len(left_tokens) <= len(right_tokens)
        else (right_tokens, left_tokens)
    )
    return len(smaller) >= 2 and smaller <= larger


# --- client ----------------------------------------------------------------------------


class NavidromeLibraryClient:
    def __init__(
        self,
        base_url: str | None,
        username: str | None,
        password: str | None,
        http: SafeHttpClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") if base_url else None
        self._username = username
        self._password = password
        self._http = http or SafeHttpClient(
            timeout_seconds=_TIMEOUT_SECONDS, max_retries=0
        )

    @property
    def configured(self) -> bool:
        """All three of URL, username and password, or the feature is simply off."""
        return bool(self._base_url and self._username and self._password)

    def _params(self) -> dict[str, str]:
        # Only reachable behind `configured`; the asserts keep mypy honest about the Optionals.
        assert self._username is not None and self._password is not None
        return auth_params(self._username, self._password)

    async def ping(self) -> bool:
        """Whether the operator credential actually works, for health/feature reporting."""
        if not self.configured:
            return False
        try:
            resp = await self._http.get(
                f"{self._base_url}/rest/ping.view",
                policy=POLICY_ALLOW_PRIVATE,
                params=self._params(),
            )
        except Exception as exc:  # noqa: BLE001 - health probes never raise
            logger.warning("navidrome_library_ping_failed", error=str(exc))
            return False
        if resp.status_code != 200:
            return False
        try:
            return is_ok(subsonic_body(resp.json()))
        except (SubsonicProtocolError, ValueError):
            return False

    async def check(self, queries: Sequence[LibraryTrackQuery]) -> LibraryCheckResult:
        """Look up every query, returning only the ones confidently found.

        Absence from `hits` means "not confidently found", which covers both a genuine miss and
        a lookup that failed. That conflation is safe in this direction and only in this
        direction: the UI shows a badge on a hit and nothing otherwise, so a failed lookup
        degrades to silence. `available` is False only when *no* lookup succeeded at all.
        """
        if not self.configured or not queries:
            return LibraryCheckResult(available=self.configured, hits={})

        # One network lookup per distinct title, not per track: a page of results routinely
        # repeats a title across sources, and the artist is matched locally anyway.
        grouped: dict[str, list[LibraryTrackQuery]] = {}
        for query in queries:
            canonical = canonical_title(query.title)
            if canonical:
                grouped.setdefault(canonical, []).append(query)
        if not grouped:
            return LibraryCheckResult(available=True, hits={})

        lookups = list(grouped.items())[:MAX_LOOKUPS]
        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

        async def run(
            group: list[LibraryTrackQuery],
        ) -> tuple[list[LibraryTrackQuery], list[dict] | None]:
            async with semaphore:
                return group, await self._search_songs(group[0].title)

        results = await asyncio.gather(*(run(group) for _, group in lookups))

        hits: dict[str, LibraryHit] = {}
        succeeded = 0
        for group, songs in results:
            if songs is None:
                continue
            succeeded += 1
            for query in group:
                match = _best_match(query, songs)
                if match is not None:
                    hits[query.key] = match

        return LibraryCheckResult(available=succeeded > 0, hits=hits)

    async def _search_songs(self, title: str) -> list[dict] | None:
        """Songs Navidrome returns for `title`, or None if the lookup itself failed.

        None and [] are different answers and the caller depends on the difference: [] is a
        real "nothing in the library matches", None is "we do not know".
        """
        params = self._params()
        params.update(
            {
                "query": title,
                "songCount": str(SONGS_PER_LOOKUP),
                "artistCount": "0",
                "albumCount": "0",
            }
        )
        try:
            resp = await self._http.get(
                f"{self._base_url}/rest/search3.view",
                policy=POLICY_ALLOW_PRIVATE,
                params=params,
            )
        except (
            Exception
        ) as exc:  # noqa: BLE001 - never let this reach the request handler
            logger.warning("navidrome_search3_failed", error=str(exc))
            return None

        if resp.status_code != 200:
            logger.warning("navidrome_search3_http_error", status=resp.status_code)
            return None
        try:
            body = subsonic_body(resp.json())
        except (SubsonicProtocolError, ValueError) as exc:
            logger.warning("navidrome_search3_bad_response", error=str(exc))
            return None
        if not is_ok(body):
            logger.warning("navidrome_search3_error", code=error_code(body))
            return None

        # A miss comes back as `"searchResult3": {}` with no `song` key at all, which is an
        # empty result and not a malformed one.
        result = body.get("searchResult3")
        if not isinstance(result, dict):
            return []
        songs = result.get("song")
        return (
            [s for s in songs if isinstance(s, dict)] if isinstance(songs, list) else []
        )


def _credited_artists(song: dict) -> list[str]:
    """Every name Navidrome credits on a song, so a match is not lost to one field's spelling.

    Navidrome fills `artist`/`displayArtist` plus structured `artists`/`albumArtists` lists; a
    compilation often carries the real performer only in one of them.
    """
    names: list[str] = []
    for key in ("artist", "displayArtist", "displayAlbumArtist"):
        value = song.get(key)
        if isinstance(value, str) and value:
            names.append(value)
    for key in ("artists", "albumArtists"):
        entries = song.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                name = entry.get("name")
                if isinstance(name, str) and name:
                    names.append(name)
    return names


def _best_match(query: LibraryTrackQuery, songs: Iterable[dict]) -> LibraryHit | None:
    """The first song agreeing on both title and artist. Both must agree; neither alone counts."""
    for song in songs:
        title = song.get("title")
        song_id = song.get("id")
        if not isinstance(title, str) or not isinstance(song_id, str) or not song_id:
            continue
        if not titles_match(query.title, title):
            continue
        credited = _credited_artists(song)
        matched = next((n for n in credited if artists_match(query.artist, n)), None)
        if matched is None:
            continue
        album = song.get("album")
        return LibraryHit(
            navidrome_id=song_id,
            title=title,
            artist=matched,
            album=album if isinstance(album, str) and album else None,
        )
    return None
