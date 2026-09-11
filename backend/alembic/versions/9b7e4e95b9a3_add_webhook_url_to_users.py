"""add webhook_url to users

Revision ID: 9b7e4e95b9a3
Revises: f058452ed5db
Create Date: 2026-09-11 00:00:00.000000

Lets a user configure a personal outbound webhook URL Cadence POSTs a small notification to
when one of their own requests is approved or fulfilled. Nullable, opt-in, no default: a plain
ADD COLUMN is enough on SQLite here (unlike the navidrome-auth migration, this one changes no
existing column's nullability, so batch_alter_table's table-rebuild isn't needed).
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b7e4e95b9a3"
down_revision: Union[str, Sequence[str], None] = "f058452ed5db"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users", sa.Column("webhook_url", sa.String(length=2048), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "webhook_url")
