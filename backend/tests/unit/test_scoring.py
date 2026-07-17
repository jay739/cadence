from __future__ import annotations

from app.services.scoring import (
    ScoredCandidate,
    best_qualifying_candidate,
    extract_extension,
    format_rank,
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
