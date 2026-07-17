"""Tag-writing step: applies metadata to the downloaded audio file before it is filed.

Prefers MusicBrainz canonical tags (fetched via app.search_sources.musicbrainz.lookup_recording)
when a musicbrainz_recording_id was captured at request time; otherwise falls back to the
title/artist/album captured from whichever source the user picked at request time.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from mutagen import File as MutagenFile
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC
from mutagen.id3 import ID3NoHeaderError
from mutagen.mp3 import MP3

logger = structlog.get_logger(__name__)


class TagWriteError(Exception):
    pass


def write_tags(file_path: Path, *, title: str, artist: str, album: str | None) -> None:
    """Write basic tags onto the file at file_path. Supports MP3 and FLAC; no-ops (logs) for
    other formats rather than raising, since tagging is best-effort polish, not the point of
    fulfillment.
    """
    suffix = file_path.suffix.lower()
    try:
        if suffix == ".mp3":
            _write_mp3_tags(file_path, title=title, artist=artist, album=album)
        elif suffix == ".flac":
            _write_flac_tags(file_path, title=title, artist=artist, album=album)
        else:
            logger.info(
                "tagging_skipped_unsupported_format", path=str(file_path), suffix=suffix
            )
    except Exception as exc:  # noqa: BLE001 - tagging failure must not fail fulfillment
        logger.warning("tagging_failed", path=str(file_path), error=str(exc))


def _write_mp3_tags(
    file_path: Path, *, title: str, artist: str, album: str | None
) -> None:
    try:
        tags = EasyID3(file_path)
    except ID3NoHeaderError:
        audio = MP3(file_path)
        audio.add_tags()
        audio.save()
        tags = EasyID3(file_path)
    tags["title"] = title
    tags["artist"] = artist
    if album:
        tags["album"] = album
    tags.save()


def _write_flac_tags(
    file_path: Path, *, title: str, artist: str, album: str | None
) -> None:
    audio = FLAC(file_path)
    audio["title"] = title
    audio["artist"] = artist
    if album:
        audio["album"] = album
    audio.save()


def probe_duration_seconds(file_path: Path) -> int | None:
    try:
        audio = MutagenFile(file_path)
    except (
        Exception
    ) as exc:  # noqa: BLE001 - a malformed/corrupt file must not raise here
        logger.warning("duration_probe_failed", path=str(file_path), error=str(exc))
        return None
    if audio is None or audio.info is None:
        return None
    return int(audio.info.length)
