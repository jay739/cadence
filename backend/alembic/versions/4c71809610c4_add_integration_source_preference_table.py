"""add integration source preference table

Revision ID: 4c71809610c4
Revises: f058452ed5db
Create Date: 2026-09-11 14:47:28.338840

Issue #18: lets an admin flip an integration to "database wins" instead of the previously
hardcoded "env always wins when set". A brand-new table rather than a column on
`integration_settings` because the preference is one-per-integration, not one-per-field, and a
missing row here means "env" -- every integration that has never touched this feature keeps
its exact previous behavior with no backfill required.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4c71809610c4"
down_revision: Union[str, Sequence[str], None] = "9b7e4e95b9a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "integration_source_preferences",
        sa.Column("integration", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("integration"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("integration_source_preferences")
