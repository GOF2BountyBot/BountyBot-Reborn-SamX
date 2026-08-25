"""Add Bronze combat bonus per-guild override columns to guild_configs.

Issue #70 Unit C: three nullable Float columns for per-guild tuning of the
Bronze post-capture duel bonus.  NULL means "use the matching GameConstants
default" (resolve_constant fallback).

Float columns (3):
  - bronze_combat_bonus_base_mult    — base fraction of winner reward (0.0–1.0)
  - bronze_combat_bonus_per_prestige — per-prestige increment (0.0–0.5)
  - bronze_combat_bonus_cap          — upper clamp on the bonus fraction (0.0–2.0)

Idempotent: inspector-guarded add_column / reversed drop_column (mirrors 0028 style).

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (column_name, sqlalchemy_type) — 3 float additive knobs for the Bronze combat bonus.
_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("bronze_combat_bonus_base_mult", sa.Float()),
    ("bronze_combat_bonus_per_prestige", sa.Float()),
    ("bronze_combat_bonus_cap", sa.Float()),
)


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
