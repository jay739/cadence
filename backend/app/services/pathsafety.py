"""Path-traversal protection for the fulfillment file-move/tag-writing step.

A maliciously crafted title/artist (from a request the admin approved, or in principle from
any upstream source's metadata) must never be able to make the final file land outside
MUSIC_LIBRARY_PATH. This module is the single choke point every write-to-library path must
pass through.

Strategy: sanitize each path *component* individually (never treat the rendered pattern as a
trusted path string), then resolve the final absolute path and verify it is still inside the
resolved library root before any filesystem operation occurs.
"""

from __future__ import annotations

import re
from pathlib import Path

_UNSAFE_CHARS = re.compile(r'[\/\\:\*\?"<>\|\x00-\x1f]')
_LEADING_DOTS = re.compile(r"^\.+")


class PathSafetyError(Exception):
    pass


def sanitize_component(value: str, *, max_length: int = 180) -> str:
    """Make a single path component (artist name, title, etc.) safe to embed in a path.

    Strips path separators, drive-letter/colon syntax, NUL and other control characters,
    reserved wildcard characters, and any leading dots (blocks '.', '..', and dotfile hiding).
    Collapses whitespace and truncates to a sane length so extremely long metadata can't be
    used to exhaust filesystem limits.
    """
    value = value.strip()
    value = _UNSAFE_CHARS.sub("_", value)
    value = _LEADING_DOTS.sub("", value)
    value = value.strip().strip(
        "."
    )  # trailing dots are also problematic on some filesystems
    value = re.sub(r"\s+", " ", value)
    if not value:
        value = "Unknown"
    return value[:max_length]


def render_library_path(
    pattern: str,
    *,
    library_root: Path,
    artist: str,
    title: str,
    ext: str,
    album: str | None = None,
) -> Path:
    """Render `pattern` (e.g. "{artist}/{title}.{ext}") into a path guaranteed inside library_root.

    Raises PathSafetyError if the rendered path would, after resolution, escape the library
    root (defense in depth on top of per-component sanitization, in case a future pattern or
    field is added carelessly).
    """
    safe_ext = sanitize_component(ext.lstrip("."), max_length=10) or "bin"
    substitutions = {
        "artist": sanitize_component(artist),
        "title": sanitize_component(title),
        "album": sanitize_component(album) if album else "Unknown Album",
        "ext": safe_ext,
    }
    try:
        rendered = pattern.format(**substitutions)
    except (KeyError, IndexError) as exc:
        raise PathSafetyError(f"Invalid library path pattern: {exc}") from exc

    # Guard against the rendered value containing '..' segments introduced by the pattern
    # itself (e.g. a pattern authored with literal ".." in it) as well as absolute paths.
    candidate = Path(rendered)
    if candidate.is_absolute():
        raise PathSafetyError("Rendered library path must be relative")

    root_resolved = library_root.resolve()
    final_path = (root_resolved / candidate).resolve()

    try:
        final_path.relative_to(root_resolved)
    except ValueError as exc:
        raise PathSafetyError(
            f"Rendered path {final_path} escapes library root {root_resolved}"
        ) from exc

    return final_path
