"""Candidate scoring for fulfillment: picking the best release/file for a requested track.

Shared, pure logic used by both the Prowlarr-candidate scorer and the Soulseek-candidate
scorer, so tests can exercise the matching/tie-break rules without any network mocking.
"""

from __future__ import annotations

import difflib
import html
import re
from dataclasses import dataclass

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _unescape_entities(value: str) -> str:
    """Indexer titles arrive HTML-escaped, and some arrive double-escaped ("&amp;amp;").

    Left alone, the stripping below turns those into literal "amp" tokens that pollute the
    similarity score for any title containing an ampersand. Unescape until stable, bounded so
    a pathological input cannot spin.
    """
    for _ in range(3):
        unescaped = html.unescape(value)
        if unescaped == value:
            break
        value = unescaped
    return value


# Formats ranked worst-to-best; used as a tiebreaker signal, never the sole criterion.
_FORMAT_RANK = {
    "mp3": 1,
    "aac": 1,
    "opus": 1,
    "m4a": 2,
    "ogg": 2,
    "flac": 4,
    "wav": 3,
    "alac": 4,
    "aiff": 3,
    "ape": 4,
    "wv": 4,
}

# What a candidate's audio actually is, independent of container naming. "unknown" is its own
# answer rather than a guess in either direction: a release title with no format marker at all
# might be lossless, and a request that asked for lossless must not be satisfied by a maybe.
QUALITY_LOSSLESS = "lossless"
QUALITY_LOSSY = "lossy"
QUALITY_UNKNOWN = "unknown"

_LOSSLESS_EXTENSIONS = {"flac", "alac", "wav", "aiff", "aif", "ape", "wv", "dsf", "dff"}
_LOSSY_EXTENSIONS = {"mp3", "aac", "m4a", "ogg", "opus", "wma", "mp4", "webm"}

# Format markers as they appear inside release titles, which usually carry no extension at all
# ("Artist - Album (2020) [FLAC 24bit]", "Artist - Title [320kbps MP3]"). Bitrate-style markers
# are listed because in practice they are the only quality signal many release titles carry.
_LOSSLESS_TOKENS = {"flac", "alac", "wav", "aiff", "ape", "wavpack", "lossless"}
_LOSSY_TOKENS = {
    "mp3",
    "aac",
    "m4a",
    "ogg",
    "opus",
    "wma",
    "lossy",
    "320",
    "320kbps",
    "256",
    "192",
    "128",
    "v0",
    "v2",
}


def normalize_text(value: str) -> str:
    value = _unescape_entities(value).lower()
    value = _NORMALIZE_RE.sub(" ", value)
    return " ".join(value.split())


def title_artist_similarity(candidate_name: str, title: str, artist: str) -> float:
    """0..1 similarity of a raw release/file name against the requested title + artist.

    Uses SequenceMatcher on normalized text as a dependency-free fuzzy match. Both title and
    artist are expected to appear somewhere in a well-formed release name; we score against
    both combination orders ("Artist - Title" and "Title - Artist") and take the best.
    """
    candidate_norm = normalize_text(candidate_name)
    combo_a = normalize_text(f"{artist} {title}")
    combo_b = normalize_text(f"{title} {artist}")
    score_a = difflib.SequenceMatcher(None, candidate_norm, combo_a).ratio()
    score_b = difflib.SequenceMatcher(None, candidate_norm, combo_b).ratio()

    title_norm = normalize_text(title)
    title_present_bonus = 0.15 if title_norm and title_norm in candidate_norm else 0.0

    return min(1.0, max(score_a, score_b) + title_present_bonus)


def extract_extension(filename: str) -> str:
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def format_rank(ext: str) -> int:
    return _FORMAT_RANK.get(ext.lower().lstrip("."), 0)


# What a requester can ask for. Deliberately not a list of containers: these three are exactly
# the promises the fulfillment tiers can keep. "any" is the neutral default -- every candidate
# counts as matching, so ordering falls through to similarity exactly as before.
DESIRED_QUALITIES = ("any", "lossy", "lossless")

# Above this, the fuzzy score has established song identity and the remaining decimals are
# noise, so swarm health is allowed to decide between candidates. Below it, identity is still
# in question and similarity has to lead.
CONFIDENT_MATCH_SIMILARITY = 0.85


def candidate_quality(raw_name: str) -> str:
    """Classify a release/file name as QUALITY_LOSSLESS, QUALITY_LOSSY, or QUALITY_UNKNOWN.

    The filename extension decides when there is a recognised one, since that is the actual
    file being offered. Otherwise the name's own format markers decide, because Prowlarr
    release titles rarely carry an extension but usually carry a "FLAC"/"320"/"MP3" style
    marker. A name with neither is QUALITY_UNKNOWN rather than being optimistically read as
    either one.
    """
    ext = extract_extension(raw_name)
    if ext in _LOSSLESS_EXTENSIONS:
        return QUALITY_LOSSLESS
    if ext in _LOSSY_EXTENSIONS:
        return QUALITY_LOSSY

    tokens = set(normalize_text(raw_name).split())
    # Lossless markers win a name that somehow carries both ("FLAC (MP3 also available)"):
    # the stronger claim is the one worth checking against the ask.
    if tokens & _LOSSLESS_TOKENS:
        return QUALITY_LOSSLESS
    if tokens & _LOSSY_TOKENS:
        return QUALITY_LOSSY
    return QUALITY_UNKNOWN


def matches_desired_quality(raw_name: str, desired: str) -> bool:
    """Whether a release/file name matches what the requester asked for, as a *preference*.

    This is the reordering signal only -- see `qualifying_candidates_ranked(require_lossless=)`
    for the one case (an unanswered lossless request) where quality actually excludes a
    candidate. Everything matches "any", so ordering then falls through to similarity exactly
    as it did before requesters could express a quality at all.
    """
    if desired == "any":
        return True
    return candidate_quality(raw_name) == desired


@dataclass
class ScoredCandidate:
    identifier: str
    raw_name: str
    similarity: float
    format_score: int
    size_bytes: int | None
    extra: dict
    matches_preference: bool = True
    # What this candidate's audio actually is (QUALITY_LOSSLESS/LOSSY/UNKNOWN). Defaults to
    # unknown so a tier that cannot tell says so rather than implying either answer; a tier
    # that knows its source can only ever be one of them (YouTube is always lossy) sets it
    # outright instead of inferring it from a name.
    quality: str = QUALITY_UNKNOWN
    # Swarm health for torrent candidates. None means the concept does not apply (Soulseek
    # transfers are peer-direct), and is treated as neutral rather than as zero.
    seeders: int | None = None

    @property
    def seed_tier(self) -> int:
        # Coarse tiers, so a healthy swarm outranks a nearly-dead one without letting raw
        # seeder count override song identity (180 vs 200 seeders should not matter, 1 vs 50
        # very much should). None sorts as the top tier so Soulseek candidates are never
        # penalised for lacking a swarm.
        if self.seeders is None:
            return 4
        if self.seeders <= 0:
            return 0
        if self.seeders < 3:
            return 1
        if self.seeders < 10:
            return 2
        if self.seeders < 50:
            return 3
        return 4

    @property
    def qualifies(self) -> bool:
        # A minimum similarity bar avoids grabbing an unrelated file just because it was the
        # only or largest result. 0.45 was chosen empirically as generous enough for messy
        # filenames (e.g. "01 - Artist_Title (Official).mp3") while rejecting clear mismatches.
        # A torrent with no seeders is excluded outright: it is not a worse choice, it is an
        # impossible one, and grabbing it burns the attempt budget waiting on metaDL forever.
        if self.seeders is not None and self.seeders <= 0:
            return False
        return self.similarity >= 0.45

    def sort_key(self) -> tuple:
        # Preference-matching candidates first (a no-op when the preference is "any", since
        # everything matches), then confident-match grouping, then swarm health, then exact
        # similarity, format, and size as deterministic tie-breaks.
        #
        # The grouping is the important part. Above CONFIDENT_MATCH_SIMILARITY the fuzzy
        # score has already established this is the right song, and the remaining decimals
        # are string-matching noise; 1 seeder versus 50 is not noise. So among confident
        # matches, swarm health decides and exact similarity only breaks the tie after it.
        # Below that threshold identity is genuinely uncertain, so similarity leads again.
        return (
            0 if self.matches_preference else 1,
            0 if self.similarity >= CONFIDENT_MATCH_SIMILARITY else 1,
            -self.seed_tier,
            -self.similarity,
            -self.format_score,
            -(self.size_bytes or 0),
        )


MIN_QUALIFYING_SIMILARITY = 0.45


def rank_candidates(candidates: list[ScoredCandidate]) -> list[ScoredCandidate]:
    return sorted(candidates, key=lambda c: c.sort_key())


def best_qualifying_candidate(
    candidates: list[ScoredCandidate],
) -> ScoredCandidate | None:
    qualifying = [c for c in candidates if c.qualifies]
    if not qualifying:
        return None
    return rank_candidates(qualifying)[0]


def qualifying_candidates_ranked(
    candidates: list[ScoredCandidate], *, require_lossless: bool = False
) -> list[ScoredCandidate]:
    """Every qualifying candidate, best first, for a tier that tries more than just #1.

    A candidate can score well and still fail for a reason scoring cannot see: a torrent stuck
    at zero seeders after the fact, a Soulseek peer that goes offline mid-transfer, a YouTube
    video pulled for a copyright claim between being indexed and being fetched. Handing back
    the full ranked list lets a fulfillment tier fall through to the next-best candidate on a
    genuine failure instead of writing off the whole tier because its single top pick didn't
    pan out, exactly the gap a real fulfillment attempt hit in production.

    `require_lossless` is the one quality gate that actually removes candidates rather than
    reordering them, and it is set only for a lossless request the requester has not (yet)
    agreed to downgrade. Anything not provably lossless is excluded, QUALITY_UNKNOWN included:
    the requester asked for a guarantee, and "this release title didn't say" is not one.
    """
    kept = [c for c in candidates if c.qualifies]
    if require_lossless:
        kept = [c for c in kept if c.quality == QUALITY_LOSSLESS]
    return rank_candidates(kept)
