"""Add platinum tier channel and role columns to guild_configs table.

Adds per-division Discord channel and role IDs for the Platinum bounty tier:
  - platinum_bounty_channel_id: Channel ID for Platinum bounty announcements
  - platinum_role_id:           Role ID for "Bounty Hunter Platinum" tier mentions

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-07
"""

import sqlalchemy as sa
from alembic import op

# Alembic revision identifiers
revision: str = "0007"
down_revision: str | None = "0006"
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
    """Add platinum_bounty_channel_id and platinum_role_id columns to guild_configs."""
    if not column_exists("guild_configs", "platinum_bounty_channel_id"):
        op.add_column(
            "guild_configs",
            sa.Column("platinum_bounty_channel_id", sa.BigInteger(), nullable=True),
        )
    if not column_exists("guild_configs", "platinum_role_id"):
        op.add_column(
            "guild_configs",
            sa.Column("platinum_role_id", sa.BigInteger(), nullable=True),
        )


def downgrade() -> None:
    """Drop platinum_bounty_channel_id and platinum_role_id columns from guild_configs."""
    if column_exists("guild_configs", "platinum_role_id"):
        op.drop_column("guild_configs", "platinum_role_id")
    if column_exists("guild_configs", "platinum_bounty_channel_id"):
        op.drop_column("guild_configs", "platinum_bounty_channel_id")
