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


def column_exists(table_name, column_name):
    """Check if a column exists in a table (for idempotent migrations)."""
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :table AND column_name = :col"),
        {"table": table_name, "col": column_name},
    )
    return result.fetchone() is not None


def index_exists(index_name):
    """Check if an index exists (for idempotent migrations)."""
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :idx"),
        {"idx": index_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    """Add reference_id column and composite index to discord_message."""
    if not column_exists("discord_message", "reference_id"):
        op.add_column("discord_message", sa.Column("reference_id", sa.BigInteger(), nullable=True))
    if not index_exists("ix_discord_message_reference"):
        op.create_index(
            "ix_discord_message_reference",
            "discord_message",
            ["guild_id", "message_type", "reference_id"],
        )


def downgrade() -> None:
    """Drop the composite index and reference_id column from discord_message."""
    if index_exists("ix_discord_message_reference"):
        op.drop_index("ix_discord_message_reference", table_name="discord_message")
    if column_exists("discord_message", "reference_id"):
        op.drop_column("discord_message", "reference_id")
