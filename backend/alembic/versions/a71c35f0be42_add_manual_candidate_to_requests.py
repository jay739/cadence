"""add manual_candidate to requests

Revision ID: a71c35f0be42
Revises: c3f2b81a47d9
Create Date: 2026-09-13 09:12:38.442117

Scoring will always sometimes pick the wrong candidate, and until now a requester who could
see that on the detail page had no way to say "no, take that one instead". This column is
where that answer is persisted: the tier to run and the single candidate to run it against,
pinned by the requester, read by the next fulfillment pass instead of a fresh re-score.

Nullable with no server default, unlike desired_quality's "any": there is no neutral manual
pick. Null means "score it normally", which is what every existing row wants and what a
request goes back to the moment a pinned attempt settles.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a71c35f0be42"
down_revision: Union[str, Sequence[str], None] = "c3f2b81a47d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Plain JSON, matching how fulfillment_attempts already persists candidate data. The
    # backend/identifier/name triple it holds is written only by the server (resolved out of
    # this request's own audit trail), never by a client payload, so nothing arriving from a
    # browser is stored here verbatim.
    op.add_column("requests", sa.Column("manual_candidate", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Any request still carrying an unconsumed pick loses it here and falls back to normal
    # scoring on its next pass, which is the pre-upgrade behaviour by definition.
    op.drop_column("requests", "manual_candidate")
