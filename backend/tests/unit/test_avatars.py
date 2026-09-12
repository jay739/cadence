"""Avatar resolution and the generated doodle.

The precedence (explicit URL, then Navidrome's picture, then a generated doodle) is the whole
feature, so each tier is pinned individually and in combination. The doodle's own properties
matter too: it has to be identical for the same user forever and different between users, or it
stops being recognisable as that person.
"""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET

from app.services.avatars import (
    SOURCE_GENERATED,
    SOURCE_NAVIDROME,
    SOURCE_URL,
    doodle_data_uri,
    doodle_svg,
    resolve_avatar,
)

SEED = "3f2a1b0c-1111-4aaa-9bbb-000000000001"


class TestPrecedence:
    def test_explicit_avatar_url_wins_over_everything(self):
        resolved = resolve_avatar(
            seed=SEED,
            avatar_url="https://example.com/me.png",
            navidrome_picture_url="https://navidrome.internal/avatar.png",
        )
        assert resolved.url == "https://example.com/me.png"
        assert resolved.source == SOURCE_URL

    def test_navidrome_picture_is_used_when_no_explicit_url(self):
        resolved = resolve_avatar(
            seed=SEED,
            avatar_url=None,
            navidrome_picture_url="https://navidrome.internal/avatar.png",
        )
        assert resolved.url == "https://navidrome.internal/avatar.png"
        assert resolved.source == SOURCE_NAVIDROME

    def test_falls_through_to_generated_doodle_when_neither_is_present(self):
        resolved = resolve_avatar(seed=SEED)
        assert resolved.source == SOURCE_GENERATED
        assert resolved.url.startswith("data:image/svg+xml;base64,")

    def test_navidrome_user_with_no_picture_gets_a_doodle(self):
        # The decision as stated: a Navidrome account without a profile picture is not left
        # blank, it gets the same generated doodle a local account would.
        resolved = resolve_avatar(
            seed=SEED, avatar_url=None, navidrome_picture_url=None
        )
        assert resolved.source == SOURCE_GENERATED

    def test_blank_and_whitespace_values_do_not_count_as_set(self):
        # An empty string is what a cleared profile field round-trips as; it must fall through
        # rather than render as a broken image.
        assert resolve_avatar(seed=SEED, avatar_url="").source == SOURCE_GENERATED
        assert resolve_avatar(seed=SEED, avatar_url="   ").source == SOURCE_GENERATED
        assert (
            resolve_avatar(seed=SEED, navidrome_picture_url="  ").source
            == SOURCE_GENERATED
        )

    def test_surrounding_whitespace_is_trimmed_from_a_real_url(self):
        resolved = resolve_avatar(seed=SEED, avatar_url="  https://example.com/a.png  ")
        assert resolved.url == "https://example.com/a.png"


class TestDoodle:
    def test_is_deterministic_for_the_same_seed(self):
        assert doodle_svg(SEED) == doodle_svg(SEED)
        assert doodle_data_uri(SEED) == doodle_data_uri(SEED)

    def test_differs_between_seeds(self):
        seeds = [f"user-{n}" for n in range(50)]
        assert len({doodle_svg(s) for s in seeds}) == len(seeds)

    def test_is_well_formed_svg(self):
        root = ET.fromstring(doodle_svg(SEED))
        assert root.tag == "{http://www.w3.org/2000/svg}svg"
        assert root.get("viewBox") == "0 0 100 100"

    def test_is_horizontally_symmetric(self):
        # The mirror is what makes an identicon read as a figure rather than as noise.
        root = ET.fromstring(doodle_svg(SEED))
        centres = set()
        for shape in root.iter():
            if shape.tag.endswith("}circle"):
                centres.add(float(shape.get("cx", "0")))
            elif shape.tag.endswith("}rect") and shape.get("rx") == "5":
                centres.add(float(shape.get("x", "0")) + 8.0)
        assert centres, "expected the doodle to contain at least one shape"
        assert {round(100 - c, 3) for c in centres} == {round(c, 3) for c in centres}

    def test_data_uri_decodes_back_to_the_svg(self):
        uri = doodle_data_uri(SEED)
        payload = uri.split(",", 1)[1]
        assert base64.b64decode(payload).decode("utf-8") == doodle_svg(SEED)

    def test_carries_no_external_reference(self):
        # A self-hosted install may have no internet at all; the doodle must never reach out.
        svg = doodle_svg(SEED)
        assert "http://" not in svg.replace("http://www.w3.org/2000/svg", "")
        assert "https://" not in svg

    def test_stays_small_enough_to_inline(self):
        assert len(doodle_data_uri(SEED)) < 4096

    def test_respects_the_requested_render_size(self):
        assert 'width="48"' in doodle_svg(SEED, size=48)
