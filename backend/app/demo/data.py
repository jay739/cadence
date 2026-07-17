"""Demo-mode fixtures: fake search results and a fake request queue.

DEMO_MODE=true makes Cadence fully exercisable (UI, screenshots, first-run evaluation) with
zero real external API keys and no live Prowlarr/slskd/qBittorrent instances. Search results
are deterministic per query (hash-seeded) so the demo still "feels" like a real catalog search
rather than always returning the same fixed list, while request-queue seeding covers every
RequestStatus value at least once.
"""

from __future__ import annotations

import hashlib

from app.models.request import RequestStatus
from app.schemas.search import SourceSearchResult, TrackResult

_DEMO_CATALOG = [
    {
        "title": "Naatu Naatu",
        "artist": "Rahul Sipligunj, Kaala Bhairava",
        "album": "RRR",
        "year": 2022,
    },
    {
        "title": "Tum Hi Ho",
        "artist": "Arijit Singh",
        "album": "Aashiqui 2",
        "year": 2013,
    },
    {
        "title": "Butta Bomma",
        "artist": "Armaan Malik",
        "album": "Ala Vaikunthapurramuloo",
        "year": 2020,
    },
    {
        "title": "Kesariya",
        "artist": "Arijit Singh",
        "album": "Brahmastra",
        "year": 2022,
    },
    {
        "title": "Blinding Lights",
        "artist": "The Weeknd",
        "album": "After Hours",
        "year": 2020,
    },
    {
        "title": "As It Was",
        "artist": "Harry Styles",
        "album": "Harry's House",
        "year": 2022,
    },
    {
        "title": "Ranjithame",
        "artist": "Anirudh Ravichander",
        "album": "Jailer",
        "year": 2023,
    },
    {
        "title": "Kannaana Kanne",
        "artist": "D. Imman",
        "album": "Viswasam",
        "year": 2019,
    },
    {"title": "Dynamite", "artist": "BTS", "album": "BE", "year": 2020},
    {
        "title": "Ilahi",
        "artist": "Arijit Singh",
        "album": "Yeh Jawaani Hai Deewani",
        "year": 2013,
    },
]

_ARTWORK_PLACEHOLDER = "https://picsum.photos/seed/{seed}/512/512"


def _seed_index(query: str, salt: str) -> int:
    digest = hashlib.sha256(f"{salt}:{query.lower()}".encode()).hexdigest()
    return int(digest, 16)


def demo_search_results(query: str) -> list[SourceSearchResult]:
    seed = _seed_index(query, "demo")
    # Pick a deterministic rotating subset/order of the catalog so different queries look
    # different, without needing any real network calls.
    ordered = sorted(_DEMO_CATALOG, key=lambda t: _seed_index(query, t["title"]))
    picks = ordered[:5]

    apple_results = [
        TrackResult(
            source="apple",
            source_id=f"demo-apple-{i}",
            title=t["title"],
            artist=t["artist"],
            album=t["album"],
            year=t["year"],
            duration_seconds=180 + (seed + i) % 90,
            artwork_url=_ARTWORK_PLACEHOLDER.format(seed=f"apple{i}{t['title']}"),
        )
        for i, t in enumerate(picks)
    ]
    musicbrainz_results = [
        TrackResult(
            source="musicbrainz",
            source_id=f"demo-mbid-{i:08x}-0000-0000-0000-000000000000",
            title=t["title"],
            artist=t["artist"],
            album=t["album"],
            year=t["year"],
            duration_seconds=180 + (seed + i) % 90,
            musicbrainz_recording_id=f"demo-mbid-{i:08x}-0000-0000-0000-000000000000",
        )
        for i, t in enumerate(picks[:4])
    ]
    spotify_results = [
        TrackResult(
            source="spotify",
            source_id=f"demo-spotify-{i}",
            title=t["title"],
            artist=t["artist"],
            album=t["album"],
            year=t["year"],
            duration_seconds=180 + (seed + i) % 90,
            artwork_url=_ARTWORK_PLACEHOLDER.format(seed=f"spotify{i}{t['title']}"),
        )
        for i, t in enumerate(picks[:3])
    ]
    prowlarr_results = [
        TrackResult(
            source="prowlarr",
            source_id=f"demo-guid-{i}",
            guid=f"demo-guid-{i}",
            title=f"{t['artist']} - {t['title']} [FLAC]",
            artist=t["artist"],
            album=t["album"],
            indexer="Demo Indexer",
            size_bytes=25_000_000 + i * 1_500_000,
            seeders=12 + i,
        )
        for i, t in enumerate(picks[:4])
    ]

    return [
        SourceSearchResult(source="apple", ok=True, results=apple_results),
        SourceSearchResult(source="musicbrainz", ok=True, results=musicbrainz_results),
        SourceSearchResult(source="spotify", ok=True, results=spotify_results),
        SourceSearchResult(source="prowlarr", ok=True, results=prowlarr_results),
    ]


DEMO_REQUEST_SEEDS = [
    {
        "title": "Naatu Naatu",
        "artist": "Rahul Sipligunj, Kaala Bhairava",
        "album": "RRR",
        "source": "apple",
        "source_id": "demo-apple-0",
        "status": RequestStatus.PENDING,
    },
    {
        "title": "Tum Hi Ho",
        "artist": "Arijit Singh",
        "album": "Aashiqui 2",
        "source": "apple",
        "source_id": "demo-apple-1",
        "status": RequestStatus.APPROVED,
    },
    {
        "title": "Kesariya",
        "artist": "Arijit Singh",
        "album": "Brahmastra",
        "source": "spotify",
        "source_id": "demo-spotify-0",
        "status": RequestStatus.DOWNLOADING,
    },
    {
        "title": "Butta Bomma",
        "artist": "Armaan Malik",
        "album": "Ala Vaikunthapurramuloo",
        "source": "musicbrainz",
        "source_id": "demo-mbid-00000002-0000-0000-0000-000000000000",
        "status": RequestStatus.FULFILLED,
        "final_file_path": "Armaan Malik/Butta Bomma.flac",
    },
    {
        "title": "Blinding Lights",
        "artist": "The Weeknd",
        "album": "After Hours",
        "source": "apple",
        "source_id": "demo-apple-4",
        "status": RequestStatus.REJECTED,
        "admin_note": "Duplicate of an existing library file.",
    },
    {
        "title": "As It Was",
        "artist": "Harry Styles",
        "album": "Harry's House",
        "source": "prowlarr",
        "source_id": "demo-guid-2",
        "status": RequestStatus.FAILED,
        "failure_reason": "No qualifying release found on configured indexers, "
        "and Soulseek fallback found no exact-title match within the attempt budget.",
    },
]
