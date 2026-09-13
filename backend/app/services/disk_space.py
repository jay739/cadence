"""Refuse to start a download that the library has no room for.

Cadence writes every fulfilled track into MUSIC_LIBRARY_PATH, which on a real deployment is a
media pool shared with everything else the operator runs. Without a check, a full pool fails
late and messily: the download completes, bandwidth and indexer budget are already spent, and
the failure surfaces as a write error at filing time (or worse, a truncated file the library
scanner picks up). Checking first turns that into an honest, actionable refusal.

Deliberately a floor on FREE BYTES rather than a percentage: what matters is whether the next
track fits, and a percentage means something completely different on a 500 GB disk than on a
4 TB pool. Set MIN_FREE_DISK_GB to 0 to disable the check entirely.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

BYTES_PER_GB = 1024**3


@dataclass(frozen=True)
class DiskSpace:
    """A point-in-time reading of the library filesystem."""

    total_bytes: int
    used_bytes: int
    free_bytes: int
    path: str

    @property
    def free_gb(self) -> float:
        return self.free_bytes / BYTES_PER_GB

    @property
    def total_gb(self) -> float:
        return self.total_bytes / BYTES_PER_GB

    @property
    def percent_used(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return 100.0 * self.used_bytes / self.total_bytes


def read_disk_space(path: Path | str) -> DiskSpace | None:
    """Usage for the filesystem holding `path`, or None if it cannot be read.

    Returns None rather than raising: an unreadable path must never be the thing that takes
    fulfillment down. Callers treat None as "unknown", which does not block a download -- a
    check that cannot run is not evidence of a full disk.
    """
    try:
        total, used, free = shutil.disk_usage(str(path))
    except OSError as exc:
        logger.warning("disk_space_unreadable", path=str(path), error=str(exc))
        return None
    return DiskSpace(
        total_bytes=total, used_bytes=used, free_bytes=free, path=str(path)
    )


def insufficient_space_reason(path: Path | str, min_free_gb: float) -> str | None:
    """Return an actionable refusal message, or None when there is room to proceed.

    None is also returned when the check is disabled (min_free_gb <= 0) or the filesystem
    cannot be read, for the reason in read_disk_space above.
    """
    if min_free_gb <= 0:
        return None
    space = read_disk_space(path)
    if space is None:
        return None
    if space.free_bytes >= min_free_gb * BYTES_PER_GB:
        return None
    return (
        f"Not enough room in the music library to download this safely: "
        f"{space.free_gb:.1f} GB free at {space.path}, and the minimum is "
        f"{min_free_gb:.1f} GB. Free some space, or lower MIN_FREE_DISK_GB if this "
        f"threshold is too conservative for your setup."
    )
