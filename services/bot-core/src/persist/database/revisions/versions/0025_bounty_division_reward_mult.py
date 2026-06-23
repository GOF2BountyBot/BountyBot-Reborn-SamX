"""Add bounty_division_reward_mult nullable JSONB column to guild_configs.

Adds ONE nullable JSONB per-guild override column for the per-division bounty
prize-pool scaler. NULL means "use GameConstants.BOUNTY_DIVISION_REWARD_MULT
default ({bronze:1.0, silver:2.0, gold:1.0, platinum:1.0})". Resolved via
resolve_constant() in the service layer and applied to the whole prize pool in
BountyService.spawn_bounty before the winner-reserve split.

Column:
- bounty_division_reward_mult  (JSONB)  — {division: float} pool multiplier

Idempotent: guards every op with inspector checks so fresh-install DBs (where
revision 0001 already materialised these columns from current ORM metadata) are
no-ops, while existing DBs receive the additive change. Mirrors revisions 0024
/ 0023 / 0022 / 0020.

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Portable JSON type: Postgres uses JSONB; SQLite test suite falls back to JSON.
_JSONB = sa.JSON().with_variant(JSONB(), "postgresql")

# (column_name, sqlalchemy_type) — single entry for the per-division pool scaler.
_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (("bounty_division_reward_mult", _JSONB),)


def _cols(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing = _cols(inspector, "guild_configs")
    for col_name, col_type in _NEW_COLUMNS:
        if col_name not in existing:
            op.add_column(
                "guild_configs",
                sa.Column(col_name, col_type, nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing = _cols(inspector, "guild_configs")
    for col_name, _col_type in reversed(_NEW_COLUMNS):
        if col_name in existing:
            op.drop_column("guild_configs", col_name)
