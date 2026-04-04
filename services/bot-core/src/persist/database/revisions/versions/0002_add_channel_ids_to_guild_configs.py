"""Add channel ID columns to guild_configs table.

Adds 4 nullable BigInteger columns to store Discord channel IDs for
announcement infrastructure: category_id, bounty_channel_id, shop_channel_id,
and general_channel_id.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op

# Alembic revision identifiers
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add 4 nullable BigInteger channel ID columns to guild_configs."""
    op.add_column("guild_configs", sa.Column("category_id", sa.BigInteger(), nullable=True))
    op.add_column("guild_configs", sa.Column("bounty_channel_id", sa.BigInteger(), nullable=True))
    op.add_column("guild_configs", sa.Column("shop_channel_id", sa.BigInteger(), nullable=True))
    op.add_column("guild_configs", sa.Column("general_channel_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    """Drop the 4 channel ID columns from guild_configs."""
    op.drop_column("guild_configs", "general_channel_id")
    op.drop_column("guild_configs", "shop_channel_id")
    op.drop_column("guild_configs", "bounty_channel_id")
    op.drop_column("guild_configs", "category_id")
