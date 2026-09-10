from __future__ import annotations

from app.services.scoring import (
    ScoredCandidate,
    best_qualifying_candidate,
    extract_extension,
    format_rank,
    matches_preferred_format,
    rank_candidates,
    title_artist_similarity,
)


class TestTitleArtistSimilarity:
    def test_exact_match_scores_high(self):
        score = title_artist_similarity(
            "Arijit Singh - Tum Hi Ho", "Tum Hi Ho", "Arijit Singh"
        )
        assert score > 0.8

    def test_reversed_order_still_scores_high(self):
        score = title_artist_similarity(
            "Tum Hi Ho - Arijit Singh", "Tum Hi Ho", "Arijit Singh"
        )
        assert score > 0.8

    def test_messy_filename_still_reasonable(self):
        score = title_artist_similarity(
            "01 - ArijitSingh_TumHiHo (Official Audio) [320kbps].mp3",
            "Tum Hi Ho",
            "Arijit Singh",
        )
        assert score > 0.3

    def test_unrelated_track_scores_low(self):
        score = title_artist_similarity(
            "Some Completely Different Song - Other Artist", "Tum Hi Ho", "Arijit Singh"
        )
        assert score < 0.45

    def test_case_and_punctuation_insensitive(self):
        a = title_artist_similarity(
            "TUM HI HO - ARIJIT SINGH!!!", "Tum Hi Ho", "Arijit Singh"
        )
        b = title_artist_similarity(
            "tum hi ho arijit singh", "Tum Hi Ho", "Arijit Singh"
        )
        assert abs(a - b) < 0.2


class TestExtensionAndFormat:
    def test_extract_extension_basic(self):
        assert extract_extension("song.mp3") == "mp3"
        assert extract_extension("Song.FLAC") == "flac"

    def test_extract_extension_no_dot(self):
        assert extract_extension("song") == ""

    def test_format_rank_flac_beats_mp3(self):
        assert format_rank("flac") > format_rank("mp3")

    def test_format_rank_unknown_is_zero(self):
        assert format_rank("xyz") == 0


class TestCandidateRankingAndQualification:
    def _candidate(
        self, similarity: float, format_score: int = 0, size: int | None = None
    ):
        return ScoredCandidate(
            identifier="id",
            raw_name="name",
            similarity=similarity,
            format_score=format_score,
            size_bytes=size,
            extra={},
        )

    def test_qualifies_above_threshold(self):
        assert self._candidate(0.5).qualifies is True

    def test_does_not_qualify_below_threshold(self):
        assert self._candidate(0.2).qualifies is False

    def test_rank_orders_by_similarity_desc(self):
        candidates = [self._candidate(0.5), self._candidate(0.9), self._candidate(0.7)]
        ranked = rank_candidates(candidates)
        assert [c.similarity for c in ranked] == [0.9, 0.7, 0.5]

    def test_tie_break_by_format_then_size(self):
        low_format_big = self._candidate(0.8, format_score=1, size=999_999_999)
        high_format_small = self._candidate(0.8, format_score=4, size=1)
        ranked = rank_candidates([low_format_big, high_format_small])
        assert ranked[0] is high_format_small

    def test_best_qualifying_candidate_returns_none_when_all_fail(self):
        candidates = [self._candidate(0.1), self._candidate(0.2)]
        assert best_qualifying_candidate(candidates) is None

    def test_best_qualifying_candidate_picks_highest_qualifying(self):
        candidates = [self._candidate(0.9), self._candidate(0.1), self._candidate(0.5)]
        winner = best_qualifying_candidate(candidates)
        assert winner is not None
        assert winner.similarity == 0.9

    def test_best_qualifying_candidate_empty_list(self):
        assert best_qualifying_candidate([]) is None


class TestFormatPreference:
    def test_any_matches_everything(self):
        assert matches_preferred_format("Artist - Title.mp3", "any") is True
        assert matches_preferred_format("whatever", "any") is True

    def test_extension_match_wins_over_tokens(self):
        assert matches_preferred_format("Artist - Title.flac", "flac") is True
        assert matches_preferred_format("Artist - Title.mp3", "flac") is False
        # A known extension is authoritative even when the name also carries other tokens.
        assert matches_preferred_format("Artist - Title FLAC rip.mp3", "flac") is False

    def test_extensionless_release_title_matches_by_token(self):
        # Prowlarr release titles rarely have an extension but usually carry a format marker.
        assert matches_preferred_format("Artist - Title (2020) [FLAC] [WEB]", "flac") is True
        assert matches_preferred_format("Artist - Title (2020) 320 MP3", "flac") is False
        assert matches_preferred_format("Artist - Title (2020) 320 MP3", "mp3") is True

    def test_preference_reorders_but_never_disqualifies(self):
        flac = ScoredCandidate(
            identifier="flac",
            raw_name="Artist - Title.flac",
            similarity=0.6,
            format_score=4,
            size_bytes=100,
            extra={},
            matches_preference=True,
        )
        mp3 = ScoredCandidate(
            identifier="mp3",
            raw_name="Artist - Title.mp3",
            similarity=0.9,
            format_score=1,
            size_bytes=100,
            extra={},
            matches_preference=False,
        )
        # The preferred format wins despite lower similarity: both already qualify.
        assert best_qualifying_candidate([mp3, flac]).identifier == "flac"
        # A lone non-matching candidate is still used rather than failing the request.
        assert best_qualifying_candidate([mp3]).identifier == "mp3"

    def test_any_preference_keeps_similarity_ordering(self):
        a = ScoredCandidate(
            identifier="a",
            raw_name="Artist - Title.mp3",
            similarity=0.9,
            format_score=1,
            size_bytes=100,
            extra={},
        )
        b = ScoredCandidate(
            identifier="b",
            raw_name="Artist - Title.flac",
            similarity=0.6,
            format_score=4,
            size_bytes=100,
            extra={},
        )
        assert best_qualifying_candidate([b, a]).identifier == "a"


def _cand(identifier: str, *, similarity: float, seeders: int | None, name: str = "x"):
    return ScoredCandidate(
        identifier=identifier,
        raw_name=name,
        similarity=similarity,
        format_score=1,
        size_bytes=100,
        extra={},
        seeders=seeders,
    )


class TestSeederAwareness:
    def test_zero_seeder_torrent_never_qualifies(self):
        # Not merely a worse pick: it can never finish, and grabbing it burns the budget.
        assert not _cand("dead", similarity=1.0, seeders=0).qualifies

    def test_soulseek_candidate_without_seeders_still_qualifies(self):
        # None means the swarm concept does not apply, and must not read as zero.
        assert _cand("slsk", similarity=0.9, seeders=None).qualifies

    def test_healthy_swarm_wins_inside_the_same_similarity_band(self):
        # The real regression: a 1.00/1-seeder release beat a 0.93/50-seeder one and stalled
        # in metaDL at 0%. Banding similarity lets swarm health settle near-ties.
        weak = _cand("weak", similarity=0.97, seeders=1)
        strong = _cand("strong", similarity=0.93, seeders=50)
        assert rank_candidates([weak, strong])[0].identifier == "strong"

    def test_similarity_still_outranks_seeders_across_bands(self):
        # A wildly-seeded wrong song must not beat the clearly-correct one.
        right = _cand("right", similarity=0.95, seeders=2)
        wrong = _cand("wrong", similarity=0.55, seeders=900)
        assert rank_candidates([right, wrong])[0].identifier == "right"


class TestEntityUnescaping:
    def test_double_escaped_ampersand_does_not_pollute_similarity(self):
        # Prowlarr hands back HTML-escaped titles, some of them double-escaped. Without
        # unescaping, the stripper leaves literal "amp" tokens behind.
        escaped = title_artist_similarity(
            "Daft Punk &amp;amp; Pharrell - Get Lucky", "Get Lucky", "Daft Punk & Pharrell"
        )
        plain = title_artist_similarity(
            "Daft Punk & Pharrell - Get Lucky", "Get Lucky", "Daft Punk & Pharrell"
        )
        assert escaped == plain
