"""Tag-writing step: applies metadata to the downloaded audio file before it is filed.

Prefers MusicBrainz canonical tags (fetched via app.search_sources.musicbrainz.lookup_recording)
when a musicbrainz_recording_id was captured at request time; otherwise falls back to the
title/artist/album captured from whichever source the user picked at request time.

Formats: MP3, FLAC, Opus and MP4/M4A. Opus matters disproportionately here because it is what
the YouTube (ytdlp-shim) tier lands, and that tier exists precisely to cover the regional
catalogue Prowlarr and Soulseek miss -- so before this module handled .opus, the tracks least
likely to be found anywhere else were also the only ones arriving in the library with no tags
at all. Anything still unhandled is logged and skipped, never raised: tagging is polish applied
after the file is already downloaded and filed, and must never fail a fulfillment that worked.

Every field is written only when it was actually obtained. An absent genre stays absent rather
than being guessed from the artist or the source, because a library full of confidently wrong
metadata is harder to fix than one with gaps.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from mutagen import File as MutagenFile
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TXXX, ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus

from app.search_sources.musicbrainz import lookup_recording, lookup_release
from app.services.cover_art import fetch_front_cover

logger = structlog.get_logger(__name__)

# Front cover. Same constant in ID3's APIC picture-type table and FLAC's picture-type table.
_PICTURE_TYPE_FRONT_COVER = 3


class TagWriteError(Exception):
    pass


@dataclass
class TrackTags:
    """Everything the tagging step knows about one track, all of it optional but title/artist.

    Fields left as None (or empty) are not written at all. That distinction is the point of
    this dataclass: "we did not learn the country" and "the country is empty" have to stay
    separate, or the tagger ends up stamping blank values over whatever the encoder wrote.
    """

    title: str
    artist: str
    album: str | None = None
    # Every credited artist, in MusicBrainz's own credit order, including featured/supporting
    # performers. `artist` above stays the primary credit so existing behaviour is unchanged.
    artists: list[str] = field(default_factory=list)
    year: int | None = None
    genre: str | None = None
    # ISO 3166-1 country of the release (e.g. "IN"), from the release lookup.
    country: str | None = None
    # ISO 639-3 language of the release's text representation (e.g. "hin", "tel").
    language: str | None = None
    cover_data: bytes | None = None
    cover_mime: str | None = None


async def collect_track_tags(
    *,
    title: str,
    artist: str,
    album: str | None,
    musicbrainz_recording_id: str | None,
) -> TrackTags:
    """Assemble the richest honest tag set available for one track.

    Starts from what the requester picked at request time (always present) and layers
    MusicBrainz canonical data over it when a recording MBID was captured. Every lookup is
    individually best-effort: MusicBrainz being down, rate-limiting, or simply not knowing a
    field leaves that field unset rather than failing the tagging step, because the file has
    already been downloaded and filed by the time this runs.

    What each source can actually provide, measured against the live API rather than assumed:
      - recording lookup: title, full artist credit (incl. featured performers), album/
        soundtrack title, release year, genre (sparse -- frequently an empty list).
      - release lookup: country and language, which are on no `inc` level of the recording.
      - Cover Art Archive: front cover, only when a release MBID was obtained.
    """
    tags = TrackTags(title=title, artist=artist, album=album)
    if not musicbrainz_recording_id:
        return tags

    mb_data = None
    try:
        mb_data = await lookup_recording(musicbrainz_recording_id)
    except Exception as exc:  # noqa: BLE001 - tagging metadata is best-effort
        logger.warning("musicbrainz_tag_lookup_failed", error=str(exc))
    if not mb_data:
        return tags

    tags.title = mb_data.get("title") or tags.title

    credits = mb_data.get("artist-credit") or []
    credited = [
        name
        for name in (
            (credit.get("artist") or {}).get("name") or credit.get("name")
            for credit in credits
        )
        if name
    ]
    if credited:
        tags.artist = credited[0]
        # Only worth recording as a list when there really is a supporting credit; a single
        # credited artist is already fully described by `artist`.
        if len(credited) > 1:
            tags.artists = credited

    release = (mb_data.get("releases") or [None])[0]
    if release:
        tags.album = release.get("title") or tags.album
        tags.year = _year_from_date(release.get("date")) or tags.year

    tags.genre = _first_genre(mb_data.get("genres"))

    release_id = release.get("id") if release else None
    if not release_id:
        return tags

    try:
        release_data = await lookup_release(release_id)
    except Exception as exc:  # noqa: BLE001 - tagging metadata is best-effort
        logger.warning("musicbrainz_release_lookup_failed", error=str(exc))
        release_data = None
    if release_data:
        tags.country = release_data.get("country") or None
        text_repr = release_data.get("text-representation") or {}
        tags.language = text_repr.get("language") or None
        if tags.genre is None:
            tags.genre = _first_genre(release_data.get("genres"))

    cover = await fetch_front_cover(release_id)
    if cover is not None:
        tags.cover_data = cover.data
        tags.cover_mime = cover.mime

    return tags


def _year_from_date(date_str: str | None) -> int | None:
    """MusicBrainz dates are "YYYY", "YYYY-MM" or "YYYY-MM-DD"; anything else yields nothing."""
    if not date_str or len(date_str) < 4 or not date_str[:4].isdigit():
        return None
    return int(date_str[:4])


def _first_genre(genres: list | None) -> str | None:
    """MusicBrainz genres are user-voted and often absent; take the most-voted one or nothing."""
    if not genres:
        return None
    ranked = sorted(genres, key=lambda g: g.get("count") or 0, reverse=True)
    return ranked[0].get("name") or None


def write_tags(
    file_path: Path,
    *,
    title: str,
    artist: str,
    album: str | None,
    tags: TrackTags | None = None,
) -> None:
    """Write tags onto the file at file_path.

    Supports MP3, FLAC, Opus and MP4/M4A; no-ops (logs) for other formats rather than raising,
    since tagging is best-effort polish, not the point of fulfillment. `tags` carries the
    richer metadata when the caller managed to obtain any; without it this writes exactly the
    title/artist/album it always did.
    """
    meta = tags or TrackTags(title=title, artist=artist, album=album)
    suffix = file_path.suffix.lower()
    try:
        if suffix == ".mp3":
            _write_mp3_tags(file_path, meta)
        elif suffix == ".flac":
            _write_flac_tags(file_path, meta)
        elif suffix == ".opus":
            _write_opus_tags(file_path, meta)
        elif suffix in (".m4a", ".mp4", ".m4b"):
            _write_mp4_tags(file_path, meta)
        else:
            logger.info(
                "tagging_skipped_unsupported_format", path=str(file_path), suffix=suffix
            )
    except Exception as exc:  # noqa: BLE001 - tagging failure must not fail fulfillment
        logger.warning("tagging_failed", path=str(file_path), error=str(exc))


def _write_mp3_tags(file_path: Path, meta: TrackTags) -> None:
    try:
        tags = EasyID3(file_path)
    except ID3NoHeaderError:
        audio = MP3(file_path)
        audio.add_tags()
        audio.save()
        tags = EasyID3(file_path)
    tags["title"] = meta.title
    tags["artist"] = meta.artist
    if meta.album:
        tags["album"] = meta.album
    # EasyID3 maps "artist" to TPE1, which is a multi-value frame: writing the full credit list
    # is how featured performers survive into players that read it.
    if meta.artists:
        tags["artist"] = meta.artists
    if meta.year is not None:
        tags["date"] = str(meta.year)
    if meta.genre:
        tags["genre"] = meta.genre
    if meta.language:
        tags["language"] = meta.language
    tags.save()

    # Country and cover art have no EasyID3 keys, so they go on through the full ID3 interface.
    if meta.country or (meta.cover_data and meta.cover_mime):
        id3 = ID3(file_path)
        if meta.country:
            # TXXX is the standard escape hatch for a value ID3 has no dedicated frame for.
            id3.add(TXXX(encoding=3, desc="Country", text=[meta.country]))
        if meta.cover_data and meta.cover_mime:
            id3.delall("APIC")
            id3.add(
                APIC(
                    encoding=3,
                    mime=meta.cover_mime,
                    type=_PICTURE_TYPE_FRONT_COVER,
                    desc="Cover",
                    data=meta.cover_data,
                )
            )
        id3.save(file_path)


def _write_flac_tags(file_path: Path, meta: TrackTags) -> None:
    audio = FLAC(file_path)
    _apply_vorbis_comments(audio, meta)
    if meta.cover_data and meta.cover_mime:
        picture = Picture()
        picture.data = meta.cover_data
        picture.type = _PICTURE_TYPE_FRONT_COVER
        picture.mime = meta.cover_mime
        picture.desc = "Cover"
        audio.clear_pictures()
        audio.add_picture(picture)
    audio.save()


def _write_opus_tags(file_path: Path, meta: TrackTags) -> None:
    audio = OggOpus(file_path)
    _apply_vorbis_comments(audio, meta)
    if meta.cover_data and meta.cover_mime:
        # Ogg has no picture block of its own: the convention (shared by Navidrome, VLC and
        # every other player that shows Opus artwork) is a FLAC Picture block, base64-encoded
        # into the METADATA_BLOCK_PICTURE comment.
        picture = Picture()
        picture.data = meta.cover_data
        picture.type = _PICTURE_TYPE_FRONT_COVER
        picture.mime = meta.cover_mime
        picture.desc = "Cover"
        audio["metadata_block_picture"] = [
            base64.b64encode(picture.write()).decode("ascii")
        ]
    audio.save()


def _apply_vorbis_comments(audio, meta: TrackTags) -> None:
    """Shared field mapping for the two Vorbis-comment formats (FLAC and Opus)."""
    audio["title"] = meta.title
    audio["artist"] = meta.artists or meta.artist
    if meta.album:
        audio["album"] = meta.album
    if meta.year is not None:
        audio["date"] = str(meta.year)
    if meta.genre:
        audio["genre"] = meta.genre
    if meta.country:
        audio["country"] = meta.country
    if meta.language:
        audio["language"] = meta.language


def _write_mp4_tags(file_path: Path, meta: TrackTags) -> None:
    audio = MP4(file_path)
    audio["\xa9nam"] = [meta.title]
    audio["\xa9ART"] = meta.artists or [meta.artist]
    if meta.album:
        audio["\xa9alb"] = [meta.album]
    if meta.year is not None:
        audio["\xa9day"] = [str(meta.year)]
    if meta.genre:
        audio["\xa9gen"] = [meta.genre]
    # MP4 has no standard country/language atoms; freeform ----:com.apple.iTunes:* atoms are
    # the established convention, and are what Navidrome and MusicBrainz Picard both use.
    if meta.country:
        audio["----:com.apple.iTunes:Country"] = [meta.country.encode("utf-8")]
    if meta.language:
        audio["----:com.apple.iTunes:Language"] = [meta.language.encode("utf-8")]
    if meta.cover_data and meta.cover_mime:
        image_format = (
            MP4Cover.FORMAT_PNG
            if meta.cover_mime == "image/png"
            else MP4Cover.FORMAT_JPEG
        )
        audio["covr"] = [MP4Cover(meta.cover_data, imageformat=image_format)]
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
