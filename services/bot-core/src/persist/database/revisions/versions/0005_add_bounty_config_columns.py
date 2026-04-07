"""Add bounty configuration columns to guild_configs table.

Adds per-guild bounty configuration:
  - bounty_max_per_tier: JSON dict of max bounties per tier {bronze, silver, gold}
  - bounty_expiry_minutes: How long a bounty lives before expiring
  - bounty_spawn_interval_minutes: How often the spawn executor checks each guild
  - next_spawn_check_at: Timestamp controlling when the guild is next eligible for spawn

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-05
"""

import sqlalchemy as sa
from alembic import op

# Alembic revision identifiers
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add 4 bounty config columns to guild_configs."""
    op.add_column(
        "guild_configs",
        sa.Column(
            "bounty_max_per_tier",
            sa.JSON(),
            nullable=True,
            server_default='{"bronze": 3, "silver": 3, "gold": 3}',
        ),
    )
    op.add_column(
        "guild_configs",
        sa.Column(
            "bounty_expiry_minutes",
            sa.Integer(),
            nullable=True,
            server_default="480",
        ),
    )
    op.add_column(
        "guild_configs",
        sa.Column(
            "bounty_spawn_interval_minutes",
            sa.Integer(),
            nullable=True,
            server_default="60",
        ),
    )
    op.add_column(
        "guild_configs",
        sa.Column(
            "next_spawn_check_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop the 4 bounty config columns from guild_configs."""
    op.drop_column("guild_configs", "next_spawn_check_at")
    op.drop_column("guild_configs", "bounty_spawn_interval_minutes")
    op.drop_column("guild_configs", "bounty_expiry_minutes")
    op.drop_column("guild_configs", "bounty_max_per_tier")
