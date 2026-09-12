"""Avatar resolution: an explicit URL, else Navidrome's picture, else a generated doodle.

WHAT NAVIDROME ACTUALLY SUPPORTS
--------------------------------
Subsonic specifies `getAvatar.view?username=`, and Navidrome does answer it -- but probing a
live Navidrome 0.63.2 showed the response is a single static placeholder PNG, byte-for-byte
identical (md5 d03032da8f321bccbe08bf014e6c4e33) for the calling user, for a *different* real
user, for a username that does not exist, and for no `username` parameter at all. Navidrome has
no per-user avatar feature, so the endpoint carries exactly zero information about the person.

There is a second, independent blocker: getAvatar needs the user's own credential, and Cadence
never stores a Navidrome password (see app.services.navidrome_auth's module docstring), so the
call could only ever be made during a login request anyway.

So in practice every Navidrome-backed account falls through to a generated doodle, which is
precisely the behaviour asked for ("if navidrome account doesn't have a profile pic, generate
the doodled avatar for the navidrome user as well"). The `navidrome_picture_url` tier below is
kept real and tested rather than deleted: it is the documented seam for the day Navidrome grows
real avatars, and wiring it up is then a single argument at the call site, not a redesign.

WHY THE DOODLE IS GENERATED HERE AND INLINE
-------------------------------------------
Cadence is self-hosted and has to work with no internet access at all, so an external avatar
service (Gravatar, DiceBear's HTTP API) is out. Generating an SVG from hashlib costs no new
dependency and no network, and returning it as a `data:` URI means the frontend just drops it
into the `<img src>` it already has -- no second rendering path, and one implementation the
backend tests can pin.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

# Bumping this changes every generated avatar at once, which is the only way to roll out a new
# look. Keep it in the hash input so the change is total and deterministic rather than partial.
_SEED_NAMESPACE = "cadence-avatar-v1"

# 5x5, mirrored down the middle: 3 generated columns produce a symmetric face-like figure, the
# same trick GitHub's identicons use. 15 cells means 15 bytes of digest, well within sha256.
_GRID = 5
_HALF_COLUMNS = (_GRID + 1) // 2  # 3
_CELL = 18
_MARGIN = 5  # 5 + 5*18 + 5 = 100, matching the viewBox below

SOURCE_URL = "url"
SOURCE_NAVIDROME = "navidrome"
SOURCE_GENERATED = "generated"


@dataclass(frozen=True)
class ResolvedAvatar:
    """The picture to actually render, plus which tier it came from.

    `source` is not decoration: the frontend uses it to decide whether an image that fails to
    load is worth reporting (a user-supplied URL that 404s) or should simply be replaced by the
    generated doodle, which can never fail because it carries no network dependency.
    """

    url: str
    source: str


def resolve_avatar(
    *,
    seed: str,
    avatar_url: str | None = None,
    navidrome_picture_url: str | None = None,
) -> ResolvedAvatar:
    """Pick an avatar by the agreed precedence: explicit URL, then Navidrome, then generated.

    `seed` must be a value that is stable for the lifetime of the account. Callers pass the
    user's primary key, deliberately *not* their email: the planned move away from
    email-as-identity would otherwise silently reshuffle everybody's avatar.
    """
    if avatar_url and avatar_url.strip():
        return ResolvedAvatar(url=avatar_url.strip(), source=SOURCE_URL)
    if navidrome_picture_url and navidrome_picture_url.strip():
        return ResolvedAvatar(
            url=navidrome_picture_url.strip(), source=SOURCE_NAVIDROME
        )
    return ResolvedAvatar(url=doodle_data_uri(seed), source=SOURCE_GENERATED)


def doodle_data_uri(seed: str) -> str:
    """The generated doodle as a self-contained `data:` URI, safe to use as an `<img src>`.

    base64 rather than percent-encoding: it is the more compact of the two for this payload and
    sidesteps any question of how the `#` in a colour or a stray quote survives being embedded
    in JSON, then HTML, then a URL.
    """
    svg = doodle_svg(seed)
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def doodle_svg(seed: str, *, size: int = 100) -> str:
    """Render the deterministic doodle for `seed` as a standalone SVG document.

    Same seed always gives the same picture, on any machine and any Python build: the only
    input is a sha256 digest, and every derived value is plain integer arithmetic on its bytes.
    """
    digest = hashlib.sha256(f"{_SEED_NAMESPACE}:{seed}".encode()).digest()

    # Two hues a good distance apart on the wheel, so the accent reads as a deliberate second
    # colour rather than a slightly-off version of the first. Lightness/saturation are fixed so
    # every avatar has the same weight and none come out muddy or invisible.
    hue = digest[0] * 360 // 256
    accent_hue = (hue + 120 + digest[1] % 90) % 360
    background = f"hsl({hue},46%,91%)"
    primary = f"hsl({hue},54%,40%)"
    accent = f"hsl({accent_hue},52%,52%)"

    primary_shapes: list[str] = []
    accent_shapes: list[str] = []
    for column in range(_HALF_COLUMNS):
        for row in range(_GRID):
            byte = digest[4 + column * _GRID + row]
            if not byte & 0b1:
                continue
            bucket = accent_shapes if byte & 0b10 else primary_shapes
            # A circle or a rounded square per cell, decided per cell, which is what gives the
            # result its hand-drawn "doodle" character instead of a uniform pixel grid.
            rounded = bool(byte & 0b100)
            for mirrored in _mirrored_columns(column):
                x = _MARGIN + mirrored * _CELL
                y = _MARGIN + row * _CELL
                bucket.append(_cell_shape(x, y, rounded=rounded))

    groups = ""
    if primary_shapes:
        groups += f'<g fill="{primary}">{"".join(primary_shapes)}</g>'
    if accent_shapes:
        groups += f'<g fill="{accent}">{"".join(accent_shapes)}</g>'

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
        f'width="{size}" height="{size}" role="img" aria-hidden="true">'
        f'<rect width="100" height="100" rx="20" fill="{background}"/>'
        f"{groups}"
        f"</svg>"
    )


def _mirrored_columns(column: int) -> tuple[int, ...]:
    """The column itself plus its mirror, deduplicated for the centre column of an odd grid."""
    mirror = _GRID - 1 - column
    return (column,) if mirror == column else (column, mirror)


def _cell_shape(x: int, y: int, *, rounded: bool) -> str:
    if rounded:
        return f'<rect x="{x + 1}" y="{y + 1}" width="{_CELL - 2}" height="{_CELL - 2}" rx="5"/>'
    return f'<circle cx="{x + _CELL // 2}" cy="{y + _CELL // 2}" r="{_CELL // 2 - 1}"/>'
