"""The already-in-library check: what counts as a match, and how it fails.

Two things are being defended here. First, the asymmetry: telling someone "you already have
this" when they do not is far worse than missing a hit, so the false-positive cases below are
as important as the true-positive ones. Second, the degradation: this decorates search, and a
broken or absent Navidrome must produce silence, never an error and never a wrong answer.
"""

from __future__ import annotations

import re

import httpx
import pytest

from app.services.navidrome_library import (
    LibraryTrackQuery,
    NavidromeLibraryClient,
    artists_match,
    canonical_title,
    titles_match,
)

# No module-level asyncio mark: pytest-asyncio runs in auto mode here, so the async tests are
# picked up on their own and the synchronous matching tests below stay unmarked.

SEARCH3 = re.compile(r"http://localhost:4533/rest/search3\.view.*")
PING = re.compile(r"http://localhost:4533/rest/ping\.view.*")


def _client(**kwargs) -> NavidromeLibraryClient:
    kwargs.setdefault("base_url", "http://localhost:4533")
    kwargs.setdefault("username", "service")
    kwargs.setdefault("password", "pw")
    return NavidromeLibraryClient(**kwargs)


def _song(title: str, artist: str, *, song_id: str = "song-1", album: str = "Album"):
    return {"id": song_id, "title": title, "artist": artist, "album": album}


def _hit_response(*songs):
    return {
        "subsonic-response": {"status": "ok", "searchResult3": {"song": list(songs)}}
    }


# Navidrome answers a miss with an empty searchResult3 object and no `song` key at all,
# verified against a live 0.63.2. That is a successful call with no results, not an error.
MISS_RESPONSE = {"subsonic-response": {"status": "ok", "searchResult3": {}}}


class TestTitleCanonicalisation:
    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("One Love", "one love"),
            ("Déjà Vu", "Deja Vu"),
            ("Song (Remastered)", "Song"),
            ("Song (Remastered 2011)", "Song"),
            ("Song - 2011 Remaster", "Song"),
            ("Song (feat. Someone)", "Song"),
            ("Song [Explicit]", "Song"),
            ("Don't Stop", "Dont Stop"),
        ],
    )
    def test_benign_decoration_is_ignored(self, left, right):
        assert titles_match(left, right)

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            # Each of these names a genuinely different recording. Collapsing them would be
            # the exact false positive this feature must not produce.
            ("Song", "Song (Live)"),
            ("Song", "Song (Acoustic)"),
            ("Song", "Song (Remix)"),
            ("Song", "Song - Radio Edit"),
            ("Song", "Song (Instrumental)"),
            ("Song", "Song (Demo)"),
            ("Song", "Another Song"),
            ("Love", "One Love"),
        ],
    )
    def test_meaningfully_different_titles_do_not_match(self, left, right):
        assert not titles_match(left, right)

    def test_a_title_that_is_only_decoration_survives(self):
        # "Remastered" as an actual title must not reduce to the empty string.
        assert canonical_title("Remastered") == "remastered"
        assert titles_match("Remastered", "Remastered")


class TestArtistMatching:
    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("Blue", "blue"),
            ("Robin S", "Robin S."),
            ("Beyoncé", "Beyonce"),
            ("The Beatles", "Beatles"),
            ("Simon & Garfunkel", "Simon and Garfunkel"),
            ("Blue feat. Elton John", "Blue"),
            ("Artist (feat. Guest)", "Artist"),
        ],
    )
    def test_equivalent_credits_match(self, left, right):
        assert artists_match(left, right)

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            # One shared token is nowhere near enough to claim someone owns a track.
            ("Blue", "Blue October"),
            ("Eagles", "Eagles of Death Metal"),
            ("Chris Brown", "Chris Stapleton"),
            ("Nirvana", "Nirvana UK"),
            ("Blue", ""),
        ],
    )
    def test_merely_overlapping_credits_do_not_match(self, left, right):
        assert not artists_match(left, right)


class TestCheck:
    async def test_reports_a_hit(self, httpx_mock):
        httpx_mock.add_response(
            url=SEARCH3, json=_hit_response(_song("One Love", "Blue", song_id="abc123"))
        )
        result = await _client().check(
            [LibraryTrackQuery(key="spotify:1", title="One Love", artist="Blue")]
        )
        assert result.available is True
        assert result.hits["spotify:1"].navidrome_id == "abc123"
        assert result.hits["spotify:1"].album == "Album"

    async def test_reports_a_miss(self, httpx_mock):
        httpx_mock.add_response(url=SEARCH3, json=MISS_RESPONSE)
        result = await _client().check(
            [
                LibraryTrackQuery(
                    key="spotify:1", title="Obscure B-Side", artist="Nobody"
                )
            ]
        )
        assert result.available is True
        assert result.hits == {}

    async def test_right_title_but_wrong_artist_is_a_miss(self, httpx_mock):
        # The dangerous case: a common title that exists in the library by someone else.
        httpx_mock.add_response(
            url=SEARCH3, json=_hit_response(_song("One Love", "Bob Marley"))
        )
        result = await _client().check(
            [LibraryTrackQuery(key="spotify:1", title="One Love", artist="Blue")]
        )
        assert result.available is True
        assert result.hits == {}

    async def test_picks_the_matching_song_out_of_several(self, httpx_mock):
        httpx_mock.add_response(
            url=SEARCH3,
            json=_hit_response(
                _song("One Love", "Bob Marley", song_id="wrong-1"),
                _song("One Love", "Someone Else", song_id="wrong-2"),
                _song("One Love", "Blue", song_id="right"),
            ),
        )
        result = await _client().check(
            [LibraryTrackQuery(key="k", title="One Love", artist="Blue")]
        )
        assert result.hits["k"].navidrome_id == "right"

    async def test_matches_against_the_structured_artists_list(self, httpx_mock):
        # Compilations often carry the real performer only in `artists`/`displayArtist`.
        song = {
            "id": "compilation-track",
            "title": "One Love",
            "artist": "Various Artists",
            "displayArtist": "Blue",
            "artists": [{"name": "Blue"}],
        }
        httpx_mock.add_response(url=SEARCH3, json=_hit_response(song))
        result = await _client().check(
            [LibraryTrackQuery(key="k", title="One Love", artist="Blue")]
        )
        assert result.hits["k"].navidrome_id == "compilation-track"

    async def test_one_lookup_serves_every_track_sharing_a_title(self, httpx_mock):
        # The same track arrives from several search sources; that must not mean several calls.
        httpx_mock.add_response(
            url=SEARCH3, json=_hit_response(_song("One Love", "Blue"))
        )
        result = await _client().check(
            [
                LibraryTrackQuery(key="spotify:1", title="One Love", artist="Blue"),
                LibraryTrackQuery(key="apple:2", title="One Love", artist="Blue"),
                LibraryTrackQuery(
                    key="mb:3", title="One Love (Remastered)", artist="Blue"
                ),
            ]
        )
        assert len(httpx_mock.get_requests()) == 1
        assert set(result.hits) == {"spotify:1", "apple:2", "mb:3"}


class TestDegradation:
    async def test_unconfigured_client_reports_unavailable(self):
        result = await NavidromeLibraryClient(None, None, None).check(
            [LibraryTrackQuery(key="k", title="One Love", artist="Blue")]
        )
        assert result.available is False
        assert result.hits == {}

    async def test_missing_credentials_alone_disable_the_check(self):
        # A Navidrome that works fine as a login backend but has no service account.
        client = NavidromeLibraryClient("http://localhost:4533", None, None)
        assert client.configured is False
        result = await client.check(
            [LibraryTrackQuery(key="k", title="One Love", artist="Blue")]
        )
        assert result.available is False

    async def test_unreachable_navidrome_reports_unavailable(self, httpx_mock):
        httpx_mock.add_exception(httpx.ConnectError("connection refused"), url=SEARCH3)
        result = await _client().check(
            [LibraryTrackQuery(key="k", title="One Love", artist="Blue")]
        )
        assert result.available is False
        assert result.hits == {}

    async def test_http_error_reports_unavailable(self, httpx_mock):
        httpx_mock.add_response(url=SEARCH3, status_code=500)
        result = await _client().check(
            [LibraryTrackQuery(key="k", title="One Love", artist="Blue")]
        )
        assert result.available is False

    async def test_rejected_credentials_report_unavailable_not_a_miss(self, httpx_mock):
        httpx_mock.add_response(
            url=SEARCH3,
            json={"subsonic-response": {"status": "failed", "error": {"code": 40}}},
        )
        result = await _client().check(
            [LibraryTrackQuery(key="k", title="One Love", artist="Blue")]
        )
        assert result.available is False

    async def test_malformed_response_reports_unavailable(self, httpx_mock):
        httpx_mock.add_response(url=SEARCH3, json={"not-subsonic": True})
        result = await _client().check(
            [LibraryTrackQuery(key="k", title="One Love", artist="Blue")]
        )
        assert result.available is False

    async def test_empty_query_list_is_not_an_error(self):
        result = await _client().check([])
        assert result.available is True
        assert result.hits == {}


class TestPing:
    async def test_ping_confirms_working_credentials(self, httpx_mock):
        httpx_mock.add_response(url=PING, json={"subsonic-response": {"status": "ok"}})
        assert await _client().ping() is True

    async def test_ping_is_false_for_rejected_credentials(self, httpx_mock):
        httpx_mock.add_response(
            url=PING,
            json={"subsonic-response": {"status": "failed", "error": {"code": 40}}},
        )
        assert await _client().ping() is False

    async def test_ping_is_false_when_unconfigured(self):
        assert await NavidromeLibraryClient(None, None, None).ping() is False
