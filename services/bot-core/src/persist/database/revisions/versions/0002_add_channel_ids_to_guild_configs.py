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


def column_exists(table_name, column_name):
    """Check if a column exists in a table (for idempotent migrations)."""
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :table AND column_name = :col"),
        {"table": table_name, "col": column_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    """Add 4 nullable BigInteger channel ID columns to guild_configs."""
    if not column_exists("guild_configs", "category_id"):
        op.add_column("guild_configs", sa.Column("category_id", sa.BigInteger(), nullable=True))
    if not column_exists("guild_configs", "bounty_channel_id"):
        op.add_column("guild_configs", sa.Column("bounty_channel_id", sa.BigInteger(), nullable=True))
    if not column_exists("guild_configs", "shop_channel_id"):
        op.add_column("guild_configs", sa.Column("shop_channel_id", sa.BigInteger(), nullable=True))
    if not column_exists("guild_configs", "general_channel_id"):
        op.add_column("guild_configs", sa.Column("general_channel_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    """Drop the 4 channel ID columns from guild_configs."""
    if column_exists("guild_configs", "general_channel_id"):
        op.drop_column("guild_configs", "general_channel_id")
    if column_exists("guild_configs", "shop_channel_id"):
        op.drop_column("guild_configs", "shop_channel_id")
    if column_exists("guild_configs", "bounty_channel_id"):
        op.drop_column("guild_configs", "bounty_channel_id")
    if column_exists("guild_configs", "category_id"):
        op.drop_column("guild_configs", "category_id")
