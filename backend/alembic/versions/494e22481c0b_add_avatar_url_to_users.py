"""add avatar_url to users

Revision ID: 494e22481c0b
Revises: f058452ed5db
Create Date: 2026-09-11 14:46:48.813314

A plain nullable column addition, unlike the navidrome-auth migration before it: SQLite can
ALTER TABLE ADD COLUMN directly for this (no NOT NULL/default change and no column being
dropped), so this does not need batch_alter_table.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "494e22481c0b"
down_revision: Union[str, Sequence[str], None] = "f058452ed5db"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users", sa.Column("avatar_url", sa.String(length=2048), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "avatar_url")
