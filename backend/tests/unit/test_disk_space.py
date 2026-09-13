from __future__ import annotations

from pathlib import Path

from app.services.disk_space import (
    BYTES_PER_GB,
    insufficient_space_reason,
    read_disk_space,
)


class TestReadDiskSpace:
    def test_reads_a_real_path(self, tmp_path: Path):
        space = read_disk_space(tmp_path)
        assert space is not None
        assert space.total_bytes > 0
        assert 0 <= space.percent_used <= 100

    def test_unreadable_path_is_unknown_rather_than_an_error(self):
        # "Unknown" must never become "blocked": a check that cannot run is not evidence of a
        # full disk, and fulfillment should not stop because of it.
        assert read_disk_space("/definitely/not/a/real/path/anywhere") is None


class TestInsufficientSpaceReason:
    def test_plenty_of_room_returns_none(self, tmp_path: Path):
        assert insufficient_space_reason(tmp_path, min_free_gb=0.000001) is None

    def test_a_threshold_above_the_disk_size_refuses(self, tmp_path: Path):
        space = read_disk_space(tmp_path)
        assert space is not None
        # Demand more than the filesystem could ever have.
        huge = (space.total_bytes / BYTES_PER_GB) * 10
        reason = insufficient_space_reason(tmp_path, min_free_gb=huge)
        assert reason is not None
        # The message has to be actionable: what is free, what was required, and the knob.
        assert "GB free" in reason
        assert "MIN_FREE_DISK_GB" in reason
        assert str(tmp_path) in reason

    def test_zero_disables_the_check(self, tmp_path: Path):
        assert insufficient_space_reason(tmp_path, min_free_gb=0) is None

    def test_unreadable_path_does_not_block(self):
        assert insufficient_space_reason("/nope/nope/nope", min_free_gb=9999) is None
