"""replace preferred_format with desired_quality

Revision ID: c3f2b81a47d9
Revises: 4c71809610c4
Create Date: 2026-09-12 10:21:04.118742

Issue #33: a request asking for FLAC was fulfilled with an Opus file. Naming a container was
always a promise Cadence could not keep -- the YouTube tier has no lossless source at all -- so
the ask becomes a generic quality instead: "any", "lossy", or "lossless", which is exactly what
the three fulfillment tiers can actually be held to.

Add-backfill-drop rather than an in-place rename: the values change meaning as well as name
("flac" -> "lossless", "mp3" -> "lossy"), so existing rows need rewriting either way, and this
keeps the whole thing to plain ALTER/UPDATE statements rather than a SQLite batch table
recreate. `lossy_accepted` arrives alongside it: an AWAITING_QUALITY request records the
requester's "a lossy copy is fine" answer there instead of overwriting what they first asked
for.

No migration is needed for the new AWAITING_QUALITY request status. SQLAlchemy stores that
column as a plain VARCHAR(16) with no CHECK constraint (see the baseline revision), and the new
member's name fits it.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f2b81a47d9"
down_revision: Union[str, Sequence[str], None] = "4c71809610c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "requests",
        sa.Column(
            "desired_quality",
            sa.String(length=16),
            server_default="any",
            nullable=False,
        ),
    )
    op.add_column(
        "requests",
        sa.Column("lossy_accepted", sa.Boolean(), server_default="0", nullable=False),
    )
    # "flac" was the only lossless option a requester could pick, "mp3" the only lossy one, and
    # anything else (including the "any" default) carries over unchanged.
    op.execute(
        "UPDATE requests SET desired_quality = 'lossless' WHERE preferred_format = 'flac'"
    )
    op.execute(
        "UPDATE requests SET desired_quality = 'lossy' WHERE preferred_format = 'mp3'"
    )
    op.drop_column("requests", "preferred_format")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "requests",
        sa.Column(
            "preferred_format",
            sa.String(length=8),
            server_default="any",
            nullable=False,
        ),
    )
    # Lossy maps back to "mp3" as the closest thing the old column could express. That is
    # lossy: a request that asked for lossless and got an Opus-era "lossy" answer reads as
    # "mp3" afterwards, which is why this direction exists for rollback only.
    op.execute(
        "UPDATE requests SET preferred_format = 'flac' WHERE desired_quality = 'lossless'"
    )
    op.execute(
        "UPDATE requests SET preferred_format = 'mp3' WHERE desired_quality = 'lossy'"
    )
    # Any request parked in AWAITING_QUALITY is moved back to a status the pre-upgrade code
    # understands. FAILED with a reason, rather than APPROVED: rolling back also rolls back the
    # tiers' refusal to downgrade, so re-queueing these would hand the requester exactly the
    # lossy file this feature exists to stop.
    op.execute(
        "UPDATE requests SET status = 'FAILED', failure_reason = "
        "'Nothing lossless was found for this request. Re-request it to try again.' "
        "WHERE status = 'AWAITING_QUALITY'"
    )
    op.drop_column("requests", "lossy_accepted")
    op.drop_column("requests", "desired_quality")
