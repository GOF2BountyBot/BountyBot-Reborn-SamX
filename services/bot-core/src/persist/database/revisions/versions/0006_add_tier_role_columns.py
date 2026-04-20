"""Add tier-specific role ID columns to guild_configs table.

Adds per-division Discord role IDs so bounty announcements can @-mention
the correct tier role:
  - bronze_role_id: Role ID for "Bounty Hunter Bronze"
  - silver_role_id: Role ID for "Bounty Hunter Silver"
  - gold_role_id:   Role ID for "Bounty Hunter Gold"

The general bounty_hunter_role_id remains for channel permission grants.
The tier roles are used exclusively for @-mentions in announcements.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-06
"""

import sqlalchemy as sa
from alembic import op

# Alembic revision identifiers
revision: str = "0006"
down_revision: str | None = "0005"
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
    """Add 3 tier role ID columns to guild_configs."""
    if not column_exists("guild_configs", "bronze_role_id"):
        op.add_column(
            "guild_configs",
            sa.Column("bronze_role_id", sa.BigInteger(), nullable=True),
        )
    if not column_exists("guild_configs", "silver_role_id"):
        op.add_column(
            "guild_configs",
            sa.Column("silver_role_id", sa.BigInteger(), nullable=True),
        )
    if not column_exists("guild_configs", "gold_role_id"):
        op.add_column(
            "guild_configs",
            sa.Column("gold_role_id", sa.BigInteger(), nullable=True),
        )


def downgrade() -> None:
    """Drop the 3 tier role ID columns from guild_configs."""
    if column_exists("guild_configs", "gold_role_id"):
        op.drop_column("guild_configs", "gold_role_id")
    if column_exists("guild_configs", "silver_role_id"):
        op.drop_column("guild_configs", "silver_role_id")
    if column_exists("guild_configs", "bronze_role_id"):
        op.drop_column("guild_configs", "bronze_role_id")
