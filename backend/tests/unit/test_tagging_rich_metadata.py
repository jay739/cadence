"""Tests for the richer tag set: extra formats, extra fields, and the honesty rules.

The .opus coverage here is deliberately built against a real Ogg/Opus stream rather than a
mocked mutagen. Opus is what the YouTube tier lands, which is the tier that exists to cover the
regional catalogue the other two miss, so "we called mutagen" is not the assurance worth having
-- "a real file came back out with real tags in it" is.

The stream below is hand-assembled (two Ogg pages: OpusHead, then OpusTags) rather than
generated with ffmpeg, so the suite keeps no binary fixtures and gains no external tool
dependency. It is a valid, if silent, Opus file as far as mutagen is concerned.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus

from app.services import cover_art as cover_art_module
from app.services import tagging as tagging_module
from app.services.cover_art import CoverArt, fetch_front_cover
from app.services.tagging import TrackTags, collect_track_tags, write_tags

# A 1x1 PNG, small enough to inline and real enough for mutagen to carry around.
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _ogg_crc(data: bytes) -> int:
    """Ogg's CRC-32 variant: polynomial 0x04c11db7, no reflection, zero init/xor."""
    poly = 0x04C11DB7
    crc = 0
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = (
                ((crc << 1) ^ poly) & 0xFFFFFFFF
                if crc & 0x80000000
                else (crc << 1) & 0xFFFFFFFF
            )
    return crc


def _ogg_page(serial: int, sequence: int, flags: int, payload: bytes) -> bytes:
    segments = []
    remaining = len(payload)
    while remaining >= 255:
        segments.append(255)
        remaining -= 255
    segments.append(remaining)
    header = (
        b"OggS"
        + bytes([0, flags])
        + struct.pack("<q", 0)
        + struct.pack("<I", serial)
        + struct.pack("<I", sequence)
        + struct.pack("<I", 0)
        + bytes([len(segments)])
        + bytes(segments)
    )
    page = header + payload
    return page[:22] + struct.pack("<I", _ogg_crc(page)) + page[26:]


def make_opus(path: Path) -> Path:
    """Write a minimal but genuinely loadable Ogg/Opus file to path."""
    opus_head = (
        b"OpusHead"
        + bytes([1, 1])
        + struct.pack("<H", 312)
        + struct.pack("<I", 48000)
        + struct.pack("<h", 0)
        + bytes([0])
    )
    opus_tags = b"OpusTags" + struct.pack("<I", 4) + b"test" + struct.pack("<I", 0)
    path.write_bytes(_ogg_page(1, 0, 2, opus_head) + _ogg_page(1, 1, 0, opus_tags))
    return path


def make_flac(path: Path) -> Path:
    """Write a minimal but genuinely loadable FLAC file (magic + STREAMINFO) to path."""
    streaminfo = (
        struct.pack(">H", 4096)  # min blocksize
        + struct.pack(">H", 4096)  # max blocksize
        + b"\x00\x00\x00"  # min framesize
        + b"\x00\x00\x00"  # max framesize
        # Packed bitfield: 20 bits sample rate (44100), 3 bits channels-1, 5 bits
        # bits-per-sample-1, then 36 bits of total-sample count.
        + bytes([0x0A, 0xC4, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00])
        + b"\x00" * 16  # md5 of the (absent) audio data
    )
    # 0x80 = last-metadata-block flag | block type 0 (STREAMINFO), then a 24-bit length.
    path.write_bytes(
        b"fLaC" + bytes([0x80]) + struct.pack(">I", len(streaminfo))[1:] + streaminfo
    )
    return path


class TestOpusIsActuallyTagged:
    """The headline fix: .opus used to fall through to the unsupported-format no-op."""

    def test_opus_file_gets_basic_tags_written(self, tmp_path: Path):
        f = make_opus(tmp_path / "song.opus")
        write_tags(f, title="Inkem Inkem", artist="Sid Sriram", album="Geetha Govindam")

        written = OggOpus(f)
        assert written["title"] == ["Inkem Inkem"]
        assert written["artist"] == ["Sid Sriram"]
        assert written["album"] == ["Geetha Govindam"]

    def test_opus_file_gets_the_full_field_set(self, tmp_path: Path):
        f = make_opus(tmp_path / "song.opus")
        tags = TrackTags(
            title="Inkem Inkem",
            artist="Sid Sriram",
            album="Geetha Govindam",
            artists=["Sid Sriram", "Gopi Sundar"],
            year=2018,
            genre="filmi",
            country="IN",
            language="tel",
            cover_data=PNG_1X1,
            cover_mime="image/png",
        )
        write_tags(f, title=tags.title, artist=tags.artist, album=tags.album, tags=tags)

        written = OggOpus(f)
        assert written["artist"] == ["Sid Sriram", "Gopi Sundar"]
        assert written["date"] == ["2018"]
        assert written["genre"] == ["filmi"]
        assert written["country"] == ["IN"]
        assert written["language"] == ["tel"]
        # Artwork rides in a base64 FLAC picture block, the Ogg convention.
        assert written["metadata_block_picture"]

    def test_opus_cover_art_round_trips_to_the_original_bytes(self, tmp_path: Path):
        import base64

        f = make_opus(tmp_path / "song.opus")
        tags = TrackTags(
            title="T", artist="A", cover_data=PNG_1X1, cover_mime="image/png"
        )
        write_tags(f, title="T", artist="A", album=None, tags=tags)

        blob = OggOpus(f)["metadata_block_picture"][0]
        picture = Picture(base64.b64decode(blob))
        assert picture.data == PNG_1X1
        assert picture.mime == "image/png"


class TestUnobtainedFieldsAreNotWritten:
    """An empty tag beats a wrong one: fields we never obtained must not be stamped on."""

    def test_opus_omits_fields_that_were_never_obtained(self, tmp_path: Path):
        f = make_opus(tmp_path / "song.opus")
        write_tags(f, title="T", artist="A", album=None)

        written = OggOpus(f)
        for absent in ("album", "date", "genre", "country", "language"):
            assert absent not in written
        assert "metadata_block_picture" not in written

    def test_flac_omits_fields_that_were_never_obtained(self, tmp_path: Path):
        f = make_flac(tmp_path / "song.flac")
        write_tags(f, title="T", artist="A", album=None)

        written = FLAC(f)
        for absent in ("album", "date", "genre", "country", "language"):
            assert absent not in written
        assert not written.pictures

    def test_mp4_omits_fields_that_were_never_obtained(self, tmp_path: Path):
        f = _make_m4a(tmp_path / "song.m4a")
        write_tags(f, title="T", artist="A", album=None)

        written = MP4(f)
        for absent in ("\xa9alb", "\xa9day", "\xa9gen", "covr"):
            assert absent not in written


class TestOtherNewFormats:
    def test_m4a_gets_tags_written(self, tmp_path: Path):
        f = _make_m4a(tmp_path / "song.m4a")
        tags = TrackTags(
            title="Title",
            artist="Artist",
            album="Album",
            year=2013,
            genre="filmi",
            country="IN",
            language="hin",
            cover_data=PNG_1X1,
            cover_mime="image/png",
        )
        write_tags(f, title=tags.title, artist=tags.artist, album=tags.album, tags=tags)

        written = MP4(f)
        assert written["\xa9nam"] == ["Title"]
        assert written["\xa9day"] == ["2013"]
        assert written["\xa9gen"] == ["filmi"]
        assert bytes(written["----:com.apple.iTunes:Country"][0]) == b"IN"
        assert bytes(written["----:com.apple.iTunes:Language"][0]) == b"hin"
        assert written["covr"]

    def test_mp3_gets_the_extra_fields(self, tmp_path: Path):
        f = tmp_path / "song.mp3"
        f.write_bytes(_silent_mp3())
        tags = TrackTags(
            title="Title",
            artist="Artist",
            album="Album",
            artists=["Artist", "Featured"],
            year=2013,
            genre="filmi",
            country="IN",
            language="hin",
            cover_data=PNG_1X1,
            cover_mime="image/png",
        )
        write_tags(f, title=tags.title, artist=tags.artist, album=tags.album, tags=tags)

        id3 = ID3(f)
        assert id3["TPE1"].text == ["Artist", "Featured"]
        assert str(id3["TDRC"].text[0]) == "2013"
        assert id3["TCON"].text == ["filmi"]
        assert id3["TXXX:Country"].text == ["IN"]
        assert id3.getall("APIC")[0].data == PNG_1X1

    def test_unsupported_extension_still_no_ops_without_raising(self, tmp_path: Path):
        f = tmp_path / "song.wav"
        original = b"RIFF____WAVEfmt not-really"
        f.write_bytes(original)
        write_tags(f, title="T", artist="A", album="Alb")
        # Untouched, and no exception: an unhandled format is skipped, never half-written.
        assert f.read_bytes() == original

    def test_corrupt_opus_does_not_raise(self, tmp_path: Path):
        f = tmp_path / "broken.opus"
        f.write_bytes(b"OggS" + b"\x00" * 40)
        write_tags(f, title="T", artist="A", album="Alb")

    def test_corrupt_m4a_does_not_raise(self, tmp_path: Path):
        f = tmp_path / "broken.m4a"
        f.write_bytes(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 40)
        write_tags(f, title="T", artist="A", album="Alb")


class TestCollectTrackTags:
    """The honesty rules, at the level that decides what write_tags is even offered."""

    async def test_without_an_mbid_nothing_is_invented(self, monkeypatch):
        async def _fail(*args, **kwargs):
            raise AssertionError("no lookup should happen without a recording MBID")

        monkeypatch.setattr(tagging_module, "lookup_recording", _fail)
        monkeypatch.setattr(tagging_module, "fetch_front_cover", _fail)

        tags = await collect_track_tags(
            title="T", artist="A", album=None, musicbrainz_recording_id=None
        )
        assert (tags.title, tags.artist, tags.album) == ("T", "A", None)
        assert tags.year is None
        assert tags.genre is None
        assert tags.country is None
        assert tags.language is None
        assert tags.cover_data is None

    async def test_full_metadata_is_collected_from_both_lookups(self, monkeypatch):
        async def _recording(recording_id, client=None):
            return {
                "title": "Inkem Inkem Kavaale",
                "artist-credit": [
                    {"artist": {"name": "Sid Sriram"}},
                    {"artist": {"name": "Gopi Sundar"}},
                ],
                "releases": [
                    {"id": "rel-1", "title": "Geetha Govindam", "date": "2018-08-15"}
                ],
                "genres": [
                    {"name": "pop", "count": 1},
                    {"name": "filmi", "count": 5},
                ],
            }

        async def _release(release_id, client=None):
            assert release_id == "rel-1"
            return {
                "country": "IN",
                "text-representation": {"language": "tel", "script": "Telu"},
            }

        async def _cover(release_id, client=None):
            return CoverArt(data=PNG_1X1, mime="image/png")

        monkeypatch.setattr(tagging_module, "lookup_recording", _recording)
        monkeypatch.setattr(tagging_module, "lookup_release", _release)
        monkeypatch.setattr(tagging_module, "fetch_front_cover", _cover)

        tags = await collect_track_tags(
            title="fallback",
            artist="fallback",
            album=None,
            musicbrainz_recording_id="3e5bc764-dfa4-454d-a2a9-0ee84ae35db2",
        )
        assert tags.title == "Inkem Inkem Kavaale"
        assert tags.artist == "Sid Sriram"
        assert tags.artists == ["Sid Sriram", "Gopi Sundar"]
        assert tags.album == "Geetha Govindam"
        assert tags.year == 2018
        # Most-voted genre wins, not merely the first one listed.
        assert tags.genre == "filmi"
        assert tags.country == "IN"
        assert tags.language == "tel"
        assert tags.cover_data == PNG_1X1

    async def test_missing_genre_country_language_are_left_unset(self, monkeypatch):
        async def _recording(recording_id, client=None):
            return {
                "title": "Some Track",
                "artist-credit": [{"artist": {"name": "Some Artist"}}],
                "releases": [{"id": "rel-1", "title": "Some Album", "date": "2011"}],
                "genres": [],
            }

        async def _release(release_id, client=None):
            return {"text-representation": {}}

        async def _no_cover(release_id, client=None):
            return None

        monkeypatch.setattr(tagging_module, "lookup_recording", _recording)
        monkeypatch.setattr(tagging_module, "lookup_release", _release)
        monkeypatch.setattr(tagging_module, "fetch_front_cover", _no_cover)

        tags = await collect_track_tags(
            title="t", artist="a", album=None, musicbrainz_recording_id="x" * 36
        )
        assert tags.year == 2011
        assert tags.genre is None
        assert tags.country is None
        assert tags.language is None
        assert tags.cover_data is None

    async def test_a_single_credit_does_not_populate_the_artists_list(
        self, monkeypatch
    ):
        async def _recording(recording_id, client=None):
            return {
                "title": "Solo",
                "artist-credit": [{"artist": {"name": "Only One"}}],
                "releases": [],
                "genres": [],
            }

        monkeypatch.setattr(tagging_module, "lookup_recording", _recording)
        tags = await collect_track_tags(
            title="t", artist="a", album=None, musicbrainz_recording_id="x" * 36
        )
        assert tags.artist == "Only One"
        assert tags.artists == []

    async def test_recording_lookup_failure_degrades_to_request_time_metadata(
        self, monkeypatch
    ):
        async def _boom(recording_id, client=None):
            raise RuntimeError("musicbrainz unreachable")

        monkeypatch.setattr(tagging_module, "lookup_recording", _boom)
        tags = await collect_track_tags(
            title="Requested Title",
            artist="Requested Artist",
            album="Requested Album",
            musicbrainz_recording_id="x" * 36,
        )
        assert tags.title == "Requested Title"
        assert tags.artist == "Requested Artist"
        assert tags.album == "Requested Album"

    async def test_release_lookup_failure_keeps_the_recording_level_fields(
        self, monkeypatch
    ):
        async def _recording(recording_id, client=None):
            return {
                "title": "Kept",
                "artist-credit": [{"artist": {"name": "Kept Artist"}}],
                "releases": [{"id": "rel-1", "title": "Kept Album", "date": "2019"}],
                "genres": [{"name": "filmi", "count": 2}],
            }

        async def _boom(release_id, client=None):
            raise RuntimeError("musicbrainz rate limited")

        async def _no_cover(release_id, client=None):
            return None

        monkeypatch.setattr(tagging_module, "lookup_recording", _recording)
        monkeypatch.setattr(tagging_module, "lookup_release", _boom)
        monkeypatch.setattr(tagging_module, "fetch_front_cover", _no_cover)

        tags = await collect_track_tags(
            title="t", artist="a", album=None, musicbrainz_recording_id="x" * 36
        )
        assert tags.album == "Kept Album"
        assert tags.year == 2019
        assert tags.genre == "filmi"
        assert tags.country is None
        assert tags.language is None

    async def test_no_release_means_no_cover_art_request_at_all(self, monkeypatch):
        async def _recording(recording_id, client=None):
            return {
                "title": "Standalone",
                "artist-credit": [{"artist": {"name": "Someone"}}],
                "releases": [],
                "genres": [],
            }

        async def _fail(*args, **kwargs):
            raise AssertionError("cover art must not be fetched without a release MBID")

        monkeypatch.setattr(tagging_module, "lookup_recording", _recording)
        monkeypatch.setattr(tagging_module, "fetch_front_cover", _fail)

        tags = await collect_track_tags(
            title="t", artist="a", album=None, musicbrainz_recording_id="x" * 36
        )
        assert tags.cover_data is None


class TestCoverArtFetch:
    """Artwork is polish: every failure mode here has to degrade to "no artwork"."""

    async def test_transport_failure_returns_none(self, monkeypatch):
        class _Client:
            async def get(self, url, **kwargs):
                raise RuntimeError("connection refused")

        assert await fetch_front_cover("rel-1", client=_Client()) is None

    async def test_404_returns_none(self, monkeypatch):
        assert (
            await fetch_front_cover("rel-1", client=_FakeClient([(404, {}, b"")]))
            is None
        )

    async def test_oversized_image_is_refused(self, monkeypatch):
        oversized = b"\x00" * 16
        monkeypatch.setattr(cover_art_module, "MAX_COVER_BYTES", 8)
        client = _FakeClient([(200, {"content-type": "image/jpeg"}, oversized)])
        assert await fetch_front_cover("rel-1", client=client) is None

    async def test_oversized_content_length_is_refused_before_reading_body(
        self, monkeypatch
    ):
        monkeypatch.setattr(cover_art_module, "MAX_COVER_BYTES", 8)
        client = _FakeClient(
            [(200, {"content-type": "image/jpeg", "content-length": "999"}, b"x" * 999)]
        )
        assert await fetch_front_cover("rel-1", client=client) is None

    async def test_non_image_content_type_is_refused(self):
        client = _FakeClient(
            [(200, {"content-type": "text/html"}, b"<html>nope</html>")]
        )
        assert await fetch_front_cover("rel-1", client=client) is None

    async def test_empty_body_is_refused(self):
        client = _FakeClient([(200, {"content-type": "image/jpeg"}, b"")])
        assert await fetch_front_cover("rel-1", client=client) is None

    async def test_redirect_chain_is_followed_to_the_image(self):
        client = _FakeClient(
            [
                (307, {"location": "https://archive.org/download/x/index.jpg"}, b""),
                (302, {"location": "https://dn1.archive.org/0/items/x/index.jpg"}, b""),
                (200, {"content-type": "image/jpeg"}, PNG_1X1),
            ]
        )
        cover = await fetch_front_cover("rel-1", client=client)
        assert cover is not None
        assert cover.data == PNG_1X1
        assert cover.mime == "image/jpeg"
        # Each hop went through the client (and so through the SSRF policy), not httpx's
        # own redirect following, which safe_http disables on purpose.
        assert len(client.requested) == 3

    async def test_redirect_without_a_location_gives_up(self):
        client = _FakeClient([(307, {}, b"")])
        assert await fetch_front_cover("rel-1", client=client) is None

    async def test_endless_redirects_terminate(self, monkeypatch):
        client = _FakeClient(
            [(307, {"location": "https://coverartarchive.org/loop"}, b"")] * 50
        )
        assert await fetch_front_cover("rel-1", client=client) is None
        assert len(client.requested) <= cover_art_module.MAX_REDIRECT_HOPS


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict, content: bytes) -> None:
        self.status_code = status_code
        self.headers = headers
        self.content = content


class _FakeClient:
    """Stands in for SafeHttpClient, recording each URL so redirect handling is observable."""

    def __init__(self, responses: list[tuple[int, dict, bytes]]) -> None:
        self._responses = list(responses)
        self.requested: list[str] = []

    async def get(self, url: str, **kwargs):
        self.requested.append(url)
        if not self._responses:
            raise AssertionError(f"unexpected extra request to {url}")
        status, headers, content = self._responses.pop(0)
        return _FakeResponse(status, headers, content)


def _make_m4a(path: Path) -> Path:
    """A minimal MP4 container mutagen will load and save tags into."""
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:  # pragma: no cover - depends on the developer's machine
        pytest.skip("ffmpeg not available to synthesize an .m4a fixture")
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            "0.1",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )
    return path


def _silent_mp3() -> bytes:
    """A handful of silent MPEG-1 Layer III frames: enough for mutagen to treat it as an MP3."""
    # 0xFF 0xFB = MPEG-1 Layer III, no CRC; 0x90 = 128kbps/44.1kHz; frame is 417 bytes.
    return (b"\xff\xfb\x90\x64" + b"\x00" * 413) * 4
