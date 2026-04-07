"""Add reference_id column and composite index to discord_message table.

Enables linking announcement messages to specific bounties (or other entities)
so they can be looked up for live-editing and deletion when bounties are
checked/expired/completed.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op

# Alembic revision identifiers
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add reference_id column and composite index to discord_message."""
    op.add_column("discord_message", sa.Column("reference_id", sa.BigInteger(), nullable=True))
    op.create_index(
        "ix_discord_message_reference",
        "discord_message",
        ["guild_id", "message_type", "reference_id"],
    )


def downgrade() -> None:
    """Drop the composite index and reference_id column from discord_message."""
    op.drop_index("ix_discord_message_reference", table_name="discord_message")
    op.drop_column("discord_message", "reference_id")
