from __future__ import annotations

from pathlib import Path

import pytest

from app.services.pathsafety import (
    PathSafetyError,
    render_library_path,
    sanitize_component,
)


class TestSanitizeComponent:
    def test_strips_path_separators(self):
        assert "/" not in sanitize_component("Artist/Name")
        assert "\\" not in sanitize_component("Artist\\Name")

    def test_blocks_parent_traversal(self):
        result = sanitize_component("..")
        assert result != ".."
        assert ".." not in result

    def test_blocks_leading_dotfile(self):
        result = sanitize_component(".hidden")
        assert not result.startswith(".")

    def test_strips_null_and_control_chars(self):
        result = sanitize_component("bad\x00name\x1f")
        assert "\x00" not in result
        assert "\x1f" not in result

    def test_strips_reserved_wildcard_chars(self):
        result = sanitize_component('weird:*?"<>|name')
        for ch in ':*?"<>|':
            assert ch not in result

    def test_empty_becomes_unknown(self):
        assert sanitize_component("") == "Unknown"
        assert sanitize_component("...") == "Unknown"

    def test_truncates_long_input(self):
        result = sanitize_component("x" * 500, max_length=180)
        assert len(result) <= 180

    def test_normal_name_passes_through_mostly_unchanged(self):
        assert sanitize_component("Arijit Singh") == "Arijit Singh"


class TestRenderLibraryPath:
    def test_renders_expected_relative_layout(self, tmp_path: Path):
        result = render_library_path(
            "{artist}/{title}.{ext}",
            library_root=tmp_path,
            artist="Arijit Singh",
            title="Tum Hi Ho",
            ext="flac",
        )
        assert result == (tmp_path / "Arijit Singh" / "Tum Hi Ho.flac").resolve()

    def test_path_traversal_via_artist_is_neutralized(self, tmp_path: Path):
        result = render_library_path(
            "{artist}/{title}.{ext}",
            library_root=tmp_path,
            artist="../../../../etc",
            title="passwd",
            ext="txt",
        )
        # Must resolve to somewhere inside tmp_path, never escape it.
        result.relative_to(tmp_path.resolve())

    def test_path_traversal_via_title_is_neutralized(self, tmp_path: Path):
        result = render_library_path(
            "{artist}/{title}.{ext}",
            library_root=tmp_path,
            artist="SomeArtist",
            title="../../../outside",
            ext="mp3",
        )
        result.relative_to(tmp_path.resolve())

    def test_absolute_path_injection_via_artist_is_neutralized(self, tmp_path: Path):
        result = render_library_path(
            "{artist}/{title}.{ext}",
            library_root=tmp_path,
            artist="/etc/passwd",
            title="Title",
            ext="mp3",
        )
        result.relative_to(tmp_path.resolve())

    def test_null_byte_injection_is_neutralized(self, tmp_path: Path):
        result = render_library_path(
            "{artist}/{title}.{ext}",
            library_root=tmp_path,
            artist="Artist\x00../../evil",
            title="Title",
            ext="mp3",
        )
        result.relative_to(tmp_path.resolve())

    def test_album_placeholder_supported(self, tmp_path: Path):
        result = render_library_path(
            "{artist}/{album}/{title}.{ext}",
            library_root=tmp_path,
            artist="Artist",
            title="Title",
            ext="mp3",
            album="Some Album",
        )
        assert result == (tmp_path / "Artist" / "Some Album" / "Title.mp3").resolve()

    def test_missing_album_falls_back(self, tmp_path: Path):
        result = render_library_path(
            "{artist}/{album}/{title}.{ext}",
            library_root=tmp_path,
            artist="Artist",
            title="Title",
            ext="mp3",
            album=None,
        )
        assert "Unknown Album" in result.parts

    def test_invalid_pattern_placeholder_raises(self, tmp_path: Path):
        with pytest.raises(PathSafetyError):
            render_library_path(
                "{nonexistent_field}/{title}.{ext}",
                library_root=tmp_path,
                artist="Artist",
                title="Title",
                ext="mp3",
            )
