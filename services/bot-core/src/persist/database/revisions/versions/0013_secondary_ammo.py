"""CI-16: Add secondary_ammo JSON sidecar column to player_ships.

Storage model: {weapon_name: remaining_rounds} per equipped secondary.
None = no ammo tracking; {} = has secondary slots but all rounds come from purchase.
The secondary_weapons list (slot identity) is unchanged.

Conservation: owned(S) = cargo.quantity(S) + Σ_ships secondary_ammo[S].
The secondary_weapons slot-list entry is pure slot occupancy — NOT a counted copy.

No backfill: no player currently has an equipped secondary weapon (starter has none;
ammo only originates from shop purchase). Default NULL (treated as {}).

Idempotent: guarded by inspector existence check (same pattern as 0011/0012).
Reversible: down() drops the column if present.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | None = None
depends_on: str | None = None

_TABLE = "player_ships"
_COLUMN = "secondary_ammo"


def upgrade() -> None:
    """Add secondary_ammo JSON column (idempotent)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(_TABLE)}
    if _COLUMN not in existing:
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    """Drop secondary_ammo column (idempotent)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(_TABLE)}
    if _COLUMN in existing:
        op.drop_column(_TABLE, _COLUMN)
