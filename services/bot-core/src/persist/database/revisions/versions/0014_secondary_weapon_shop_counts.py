"""CI-11: Add dedicated secondary_weapon shop count/quantity range columns to guild_configs.

Gives secondary_weapon its own shop allocation (secondary_weapon_count_range,
secondary_weapon_quantity_range) instead of piggybacking on the weapon ranges.
Default mirrors weapon: count min:3/max:5, quantity min:2/max:4.

R1 backfill: add_column NULL-fills pre-existing rows; SQLAlchemy server_default only
fires on INSERT, so this migration explicitly backfills NULLs to keep
random.randint(range["min"], ...) from raising TypeError on existing guilds.

Idempotent: inspector-guarded add_column (same pattern as 0013).
Reversible: down() drops both columns if present.

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | None = None
depends_on: str | None = None

_TABLE = "guild_configs"
_COUNT_COL = "secondary_weapon_count_range"
_QTY_COL = "secondary_weapon_quantity_range"


def upgrade() -> None:
    """Add secondary_weapon_count_range and secondary_weapon_quantity_range columns (idempotent)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(_TABLE)}

    if _COUNT_COL not in existing:
        op.add_column(
            _TABLE,
            sa.Column(_COUNT_COL, sa.JSON(), nullable=True),
        )

    if _QTY_COL not in existing:
        op.add_column(
            _TABLE,
            sa.Column(_QTY_COL, sa.JSON(), nullable=True),
        )

    # R1 backfill: pre-existing rows get NULL from add_column; backfill to defaults so
    # random.randint(count_range["min"], ...) never raises TypeError on None["min"].
    op.execute(
        sa.text(
            f"UPDATE {_TABLE} SET {_COUNT_COL} = '{{\"min\":3,\"max\":5}}'::jsonb "
            f"WHERE {_COUNT_COL} IS NULL"
        )
    )
    op.execute(
        sa.text(
            f"UPDATE {_TABLE} SET {_QTY_COL} = '{{\"min\":2,\"max\":4}}'::jsonb "
            f"WHERE {_QTY_COL} IS NULL"
        )
    )


def downgrade() -> None:
    """Drop secondary_weapon count/quantity range columns (idempotent)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(_TABLE)}

    if _QTY_COL in existing:
        op.drop_column(_TABLE, _QTY_COL)

    if _COUNT_COL in existing:
        op.drop_column(_TABLE, _COUNT_COL)
