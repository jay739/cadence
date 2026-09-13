"""add download progress fields to requests

Revision ID: a71c5e2f9d34
Revises: c3f2b81a47d9
Create Date: 2026-09-13 09:14:22.481903

Issue #8: a download in flight showed nothing but "Downloading", with no way to tell a transfer
that is 90% done from one that has been stalled since it started.

Two nullable columns rather than one percentage, because only two of the three fulfillment
tiers can report a real fraction. qBittorrent exposes `progress` (0..1) and slskd exposes
`percentComplete` (0..100) on the very objects Cadence already polls for completion, so those
tiers fill in `download_progress_percent`. The ytdlp-shim tier exposes nothing but a job state,
so it leaves that column NULL and says what it is doing in `download_phase` instead. NULL
therefore means "no honest number exists here", which is why the percentage column is nullable
rather than defaulting to 0: an unknown fraction and a download that has moved no bytes are
different facts, and rendering them identically is the bar-that-lies this feature exists to
avoid.

No backfill. Every row predating this migration is either finished (nothing in flight to
describe) or was left mid-download by a worker that has since restarted, and
FulfillmentWorker.recover_crashed_jobs resets those to APPROVED to be re-evaluated from
scratch. A backfilled value would describe a download that is no longer running.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a71c5e2f9d34"
down_revision: Union[str, Sequence[str], None] = "c3f2b81a47d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "requests",
        sa.Column("download_progress_percent", sa.Float(), nullable=True),
    )
    op.add_column(
        "requests",
        sa.Column("download_phase", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("requests", "download_phase")
    op.drop_column("requests", "download_progress_percent")
