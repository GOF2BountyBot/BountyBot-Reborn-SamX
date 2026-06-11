"""Drop player_ships.manual_turret_mode — turret/primary switching is now range-driven.

The static per-ship manual_turret_mode flag (added in 0011, §6.3) is retired:
the combat engine now switches between primaries and manual turrets per tick
based on range alone (manual turrets fire only while no primary is in range).
No writer for this column ever existed (the UI toggle was never implemented),
so every row holds the server default `false` and no data is lost.

Idempotent: guards the DROP with an inspector existence check (same pattern as 0012).
Safe to run on fresh-install DBs where the column may already be absent.

Reversible: down() re-adds the column with its original definition (NOT NULL,
server_default false). No data restoration needed — the column was always false.

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | None = None
depends_on: str | None = None

_TABLE = "player_ships"
_COLUMN = "manual_turret_mode"


def upgrade() -> None:
    """Drop the retired manual_turret_mode column (idempotent)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(_TABLE)}
    if _COLUMN in existing:
        op.drop_column(_TABLE, _COLUMN)


def downgrade() -> None:
    """Re-add manual_turret_mode with its original 0011 definition."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(_TABLE)}
    if _COLUMN not in existing:
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.Boolean(), nullable=False, server_default="false"),
        )
