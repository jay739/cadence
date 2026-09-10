"""navidrome auth: nullable password hash, auth provider, external username

Revision ID: f058452ed5db
Revises: 064714fe9f5c
Create Date: 2026-09-10 01:43:47.055334

Externally-authenticated users have no password stored here at all, so `password_hash` has to
become nullable. SQLite cannot ALTER a column, so every change goes through batch_alter_table,
which recreates the table and copies the rows.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f058452ed5db"
down_revision: Union[str, Sequence[str], None] = "064714fe9f5c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "auth_provider",
                sa.Enum(
                    "LOCAL",
                    "NAVIDROME",
                    name="authprovider",
                    native_enum=False,
                    length=16,
                ),
                server_default="local",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("external_username", sa.String(length=255), nullable=True)
        )
        batch_op.alter_column(
            "password_hash", existing_type=sa.VARCHAR(length=255), nullable=True
        )
        batch_op.create_index(
            batch_op.f("ix_users_external_username"),
            ["external_username"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema.

    Navidrome-backed rows have a null password_hash and cannot survive the NOT NULL being
    restored. They also could not authenticate after this downgrade, since the provider column
    they depend on is dropped here, so removing them is the only coherent outcome rather than
    leaving rows that fail the constraint or silently become unusable local accounts.
    """
    op.execute("DELETE FROM users WHERE auth_provider = 'navidrome'")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_external_username"))
        batch_op.alter_column(
            "password_hash", existing_type=sa.VARCHAR(length=255), nullable=False
        )
        batch_op.drop_column("external_username")
        batch_op.drop_column("auth_provider")
