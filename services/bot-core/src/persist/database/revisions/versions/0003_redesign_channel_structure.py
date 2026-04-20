"""Redesign channel structure in guild_configs table.

Removes bounty_channel_id and general_channel_id; adds 7 new channel-specific
columns and a bounty_hunter_role_id column.  Data is migrated: bounty_channel_id
→ bronze_bounty_channel_id and general_channel_id → hunting_channel_id.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op

# Alembic revision identifiers
revision: str = "0003"
down_revision: str | None = "0002"
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
    """Add 7 new channel columns + role column, migrate data, drop 2 old columns."""
    # Add new columns (all nullable BigInteger)
    if not column_exists("guild_configs", "bronze_bounty_channel_id"):
        op.add_column("guild_configs", sa.Column("bronze_bounty_channel_id", sa.BigInteger(), nullable=True))
    if not column_exists("guild_configs", "silver_bounty_channel_id"):
        op.add_column("guild_configs", sa.Column("silver_bounty_channel_id", sa.BigInteger(), nullable=True))
    if not column_exists("guild_configs", "gold_bounty_channel_id"):
        op.add_column("guild_configs", sa.Column("gold_bounty_channel_id", sa.BigInteger(), nullable=True))
    if not column_exists("guild_configs", "hunting_channel_id"):
        op.add_column("guild_configs", sa.Column("hunting_channel_id", sa.BigInteger(), nullable=True))
    if not column_exists("guild_configs", "discussion_channel_id"):
        op.add_column("guild_configs", sa.Column("discussion_channel_id", sa.BigInteger(), nullable=True))
    if not column_exists("guild_configs", "image_channel_id"):
        op.add_column("guild_configs", sa.Column("image_channel_id", sa.BigInteger(), nullable=True))
    if not column_exists("guild_configs", "bounty_hunter_role_id"):
        op.add_column("guild_configs", sa.Column("bounty_hunter_role_id", sa.BigInteger(), nullable=True))

    # Migrate existing data: bounty_channel_id → bronze_bounty_channel_id
    # Only run if the source column exists (it won't on fresh databases where 0001 already has both columns)
    if column_exists("guild_configs", "bounty_channel_id"):
        op.execute(
            "UPDATE guild_configs SET bronze_bounty_channel_id = bounty_channel_id WHERE bounty_channel_id IS NOT NULL"
        )

    # Migrate existing data: general_channel_id → hunting_channel_id
    # Only run if the source column exists (it won't on fresh databases where 0001 already has both columns)
    if column_exists("guild_configs", "general_channel_id"):
        op.execute(
            "UPDATE guild_configs SET hunting_channel_id = general_channel_id WHERE general_channel_id IS NOT NULL"
        )

    # Drop old columns (only if they exist)
    if column_exists("guild_configs", "bounty_channel_id"):
        op.drop_column("guild_configs", "bounty_channel_id")
    if column_exists("guild_configs", "general_channel_id"):
        op.drop_column("guild_configs", "general_channel_id")


def downgrade() -> None:
    """Re-add 2 old columns, migrate data back, drop 7 new columns."""
    # Re-add the old columns
    if not column_exists("guild_configs", "bounty_channel_id"):
        op.add_column("guild_configs", sa.Column("bounty_channel_id", sa.BigInteger(), nullable=True))
    if not column_exists("guild_configs", "general_channel_id"):
        op.add_column("guild_configs", sa.Column("general_channel_id", sa.BigInteger(), nullable=True))

    # Migrate data back: bronze_bounty_channel_id → bounty_channel_id
    if column_exists("guild_configs", "bronze_bounty_channel_id"):
        op.execute(
            "UPDATE guild_configs SET bounty_channel_id = bronze_bounty_channel_id "
            "WHERE bronze_bounty_channel_id IS NOT NULL"
        )

    # Migrate data back: hunting_channel_id → general_channel_id
    if column_exists("guild_configs", "hunting_channel_id"):
        op.execute(
            "UPDATE guild_configs SET general_channel_id = hunting_channel_id WHERE hunting_channel_id IS NOT NULL"
        )

    # Drop new columns (only if they exist)
    if column_exists("guild_configs", "bounty_hunter_role_id"):
        op.drop_column("guild_configs", "bounty_hunter_role_id")
    if column_exists("guild_configs", "image_channel_id"):
        op.drop_column("guild_configs", "image_channel_id")
    if column_exists("guild_configs", "discussion_channel_id"):
        op.drop_column("guild_configs", "discussion_channel_id")
    if column_exists("guild_configs", "hunting_channel_id"):
        op.drop_column("guild_configs", "hunting_channel_id")
    if column_exists("guild_configs", "gold_bounty_channel_id"):
        op.drop_column("guild_configs", "gold_bounty_channel_id")
    if column_exists("guild_configs", "silver_bounty_channel_id"):
        op.drop_column("guild_configs", "silver_bounty_channel_id")
    if column_exists("guild_configs", "bronze_bounty_channel_id"):
        op.drop_column("guild_configs", "bronze_bounty_channel_id")
