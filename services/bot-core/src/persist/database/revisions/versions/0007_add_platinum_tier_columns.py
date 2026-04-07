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


def upgrade() -> None:
    """Add platinum_bounty_channel_id and platinum_role_id columns to guild_configs."""
    op.add_column(
        "guild_configs",
        sa.Column("platinum_bounty_channel_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "guild_configs",
        sa.Column("platinum_role_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    """Drop platinum_bounty_channel_id and platinum_role_id columns from guild_configs."""
    op.drop_column("guild_configs", "platinum_role_id")
    op.drop_column("guild_configs", "platinum_bounty_channel_id")
