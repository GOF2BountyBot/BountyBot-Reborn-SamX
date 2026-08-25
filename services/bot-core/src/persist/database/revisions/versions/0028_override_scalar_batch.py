"""Add D-trivial + DIVISION_TL_CENTERS scalar override columns to guild_configs.

Additive batch (issue #70): 18 nullable columns, no backfill, no drops.
NULL means "use the matching GameConstants default" (resolve_constant fallback).

Integer columns (15):
  - criminal_secondary_min_damage     — min real damage for criminal secondary selection
  - shop_secondary_qty_scaler_heavy   — per-refresh quantity scaler for heavy ordnance
  - shop_secondary_qty_scaler_standard — per-refresh quantity scaler for standard ammo
  - shop_tl_band_lo_bronze            — in-band TL range lower bound (Bronze)
  - shop_tl_band_hi_bronze            — in-band TL range upper bound (Bronze)
  - shop_tl_band_lo_silver            — in-band TL range lower bound (Silver)
  - shop_tl_band_hi_silver            — in-band TL range upper bound (Silver)
  - shop_tl_band_lo_gold              — in-band TL range lower bound (Gold)
  - shop_tl_band_hi_gold              — in-band TL range upper bound (Gold)
  - shop_tl_band_lo_platinum          — in-band TL range lower bound (Platinum)
  - shop_tl_band_hi_platinum          — in-band TL range upper bound (Platinum)
  - division_tl_center_bronze         — TL draw centre for Bronze criminal spawns
  - division_tl_center_silver         — TL draw centre for Silver criminal spawns
  - division_tl_center_gold           — TL draw centre for Gold criminal spawns
  - division_tl_center_platinum       — TL draw centre for Platinum criminal spawns

Float columns (3):
  - shop_banded_tl_weight             — in-band fraction for shop batch TL draw
  - shop_uptier_tl_decay              — exponential decay above band_hi
  - shop_downtier_tl_decay            — exponential decay below band_lo

Idempotent: inspector-guarded add_column / reversed drop_column (mirrors 0026 style).

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (column_name, sqlalchemy_type) — 15 integer + 3 float additive knobs.
_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    # Criminal loadout — secondary selection
    ("criminal_secondary_min_damage", sa.Integer()),
    # Shop secondary quantity scalers
    ("shop_secondary_qty_scaler_heavy", sa.Integer()),
    ("shop_secondary_qty_scaler_standard", sa.Integer()),
    # Shop TL band bounds (per tier)
    ("shop_tl_band_lo_bronze", sa.Integer()),
    ("shop_tl_band_hi_bronze", sa.Integer()),
    ("shop_tl_band_lo_silver", sa.Integer()),
    ("shop_tl_band_hi_silver", sa.Integer()),
    ("shop_tl_band_lo_gold", sa.Integer()),
    ("shop_tl_band_hi_gold", sa.Integer()),
    ("shop_tl_band_lo_platinum", sa.Integer()),
    ("shop_tl_band_hi_platinum", sa.Integer()),
    # Division TL draw centres (flatten of DIVISION_TL_CENTERS dict)
    ("division_tl_center_bronze", sa.Integer()),
    ("division_tl_center_silver", sa.Integer()),
    ("division_tl_center_gold", sa.Integer()),
    ("division_tl_center_platinum", sa.Integer()),
    # Shop banded TL weight + taper decays (float)
    ("shop_banded_tl_weight", sa.Float()),
    ("shop_uptier_tl_decay", sa.Float()),
    ("shop_downtier_tl_decay", sa.Float()),
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
