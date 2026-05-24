"""Add demotion_credit_penalty_pct to guild_configs.

NULL means "use the global GameConstants.DEMOTION_CREDIT_PENALTY_PCT default (10%)".
Matches the B.49 nullable-override pattern used by all other per-guild game-balance
columns on guild_configs.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-24

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add demotion_credit_penalty_pct column to guild_configs.

    Guards against fresh installs where migration 0001 already creates all
    tables from current ORM metadata (including this column), which would
    cause a ProgrammingError: column already exists on op.add_column().
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = [col["name"] for col in inspector.get_columns("guild_configs")]
    if "demotion_credit_penalty_pct" not in existing_columns:
        op.add_column(
            "guild_configs",
            sa.Column("demotion_credit_penalty_pct", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    """Remove demotion_credit_penalty_pct column from guild_configs."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = [col["name"] for col in inspector.get_columns("guild_configs")]
    if "demotion_credit_penalty_pct" in existing_columns:
        op.drop_column("guild_configs", "demotion_credit_penalty_pct")
