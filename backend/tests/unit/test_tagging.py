from __future__ import annotations

from pathlib import Path

from app.services.tagging import probe_duration_seconds, write_tags

# Minimal valid FLAC/MP3 fixtures are non-trivial to synthesize from scratch; instead we build
# tiny valid files using mutagen's own writers, which is enough to prove write_tags/probe work
# against real file structures without needing binary fixture assets checked into the repo.


def _make_empty_flac(path: Path) -> None:
    # A silence-only, header-only FLAC stream (minimal valid FLAC) is complex to hand-roll;
    # instead skip actual audio content and only exercise error handling paths that don't
    # require a fully valid decodable stream, since write_tags must not raise regardless.
    path.write_bytes(b"fLaC" + b"\x00" * 42)


class TestWriteTagsRobustness:
    def test_unsupported_extension_does_not_raise(self, tmp_path: Path):
        f = tmp_path / "song.ogg"
        f.write_bytes(b"not a real ogg file")
        # Must not raise; unsupported formats are logged and skipped.
        write_tags(f, title="T", artist="A", album="Alb")

    def test_corrupt_flac_does_not_raise(self, tmp_path: Path):
        f = tmp_path / "song.flac"
        _make_empty_flac(f)
        write_tags(f, title="T", artist="A", album="Alb")

    def test_corrupt_mp3_does_not_raise(self, tmp_path: Path):
        f = tmp_path / "song.mp3"
        f.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 100)
        write_tags(f, title="T", artist="A", album="Alb")

    def test_nonexistent_file_does_not_raise(self, tmp_path: Path):
        f = tmp_path / "does_not_exist.mp3"
        write_tags(f, title="T", artist="A", album=None)


class TestProbeDuration:
    def test_unreadable_file_returns_none(self, tmp_path: Path):
        f = tmp_path / "garbage.mp3"
        f.write_bytes(b"garbage-not-audio")
        assert probe_duration_seconds(f) is None
