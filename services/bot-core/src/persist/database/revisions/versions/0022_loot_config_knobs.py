"""Add PvC loot tunable-knob override columns to guild_configs (LOOT_JOURNAL §8 / T2).

Adds nineteen nullable per-guild override columns mirroring the B.49 / 0020 NULL-means-default
pattern. NULL on any column means "use the matching GameConstants default" — the service layer
resolves the actual value via resolve_constant().

Columns (all nullable, default NULL):
- loot_chance_tractor_t1          (Integer)  — §5.3 loot-roll % with AB-1 Retractor (0–100)
- loot_chance_tractor_t2          (Integer)  — §5.3 loot-roll % with AB-2 Glue Gun (0–100)
- loot_chance_tractor_t3          (Integer)  — §5.3 loot-roll % with AB-3 Kingfisher (0–100)
- loot_chance_tractor_t4          (Integer)  — §5.3 loot-roll % with AB-4 Octopus (0–100)
- loot_chance_no_tractor          (Integer)  — §5.3 loot-roll % with no beam equipped (0–100)
- loot_band1_select_pct           (Integer)  — §5.8.4 Band-1 (Weapons+Modules) select % (0–100)
- loot_band2_select_pct           (Integer)  — §5.8.4 Band-2 (ore_core,rare) select % (0–100)
- loot_band3_select_pct           (Integer)  — §5.8.4 Band-3 (bulk commodities) select % (0–100)
- loot_band1_tl_window            (Integer)  — §5.8.4 Band-1 ±TL window vs criminal TL
- loot_band1_qty_min              (Integer)  — §5.8.1 Band-1 min qty
- loot_band1_qty_max              (Integer)  — §5.8.1 Band-1 max qty
- loot_band1_qty_mode             (Integer)  — §5.8.1 Band-1 triangular mode
- loot_band2_qty_min              (Integer)  — §5.8.2 Band-2 min qty
- loot_band2_qty_max              (Integer)  — §5.8.2 Band-2 max qty
- loot_band2_qty_mode             (Integer)  — §5.8.2 Band-2 triangular mode
- loot_band3_qty_min              (Integer)  — §5.8.3 Band-3 min qty
- loot_band3_qty_max              (Integer)  — §5.8.3 Band-3 max qty
- loot_band3_qty_mode             (Integer)  — §5.8.3 Band-3 triangular mode
- loot_commodity_sell_fraction    (Float)    — §5.7 commodity sell payout fraction (0.0–…)

Idempotent: guards every op with inspector checks so fresh-install DBs (where revision
0001 already materialised these columns from current ORM metadata) are no-ops, while
existing DBs receive the additive change. Mirrors revisions 0020 / 0021.

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (column_name, sqlalchemy_type) — order is the apply order; reversed for downgrade.
# 18 integer knobs (percentages 0–100, TL window, and qty min/max/mode) + 1 float
# (commodity sell fraction). LOOT_DROP_CHANCE is a FIXED constant (m-5) and gets NO column.
_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("loot_chance_tractor_t1", sa.Integer()),
    ("loot_chance_tractor_t2", sa.Integer()),
    ("loot_chance_tractor_t3", sa.Integer()),
    ("loot_chance_tractor_t4", sa.Integer()),
    ("loot_chance_no_tractor", sa.Integer()),
    ("loot_band1_select_pct", sa.Integer()),
    ("loot_band2_select_pct", sa.Integer()),
    ("loot_band3_select_pct", sa.Integer()),
    ("loot_band1_tl_window", sa.Integer()),
    ("loot_band1_qty_min", sa.Integer()),
    ("loot_band1_qty_max", sa.Integer()),
    ("loot_band1_qty_mode", sa.Integer()),
    ("loot_band2_qty_min", sa.Integer()),
    ("loot_band2_qty_max", sa.Integer()),
    ("loot_band2_qty_mode", sa.Integer()),
    ("loot_band3_qty_min", sa.Integer()),
    ("loot_band3_qty_max", sa.Integer()),
    ("loot_band3_qty_mode", sa.Integer()),
    ("loot_commodity_sell_fraction", sa.Float()),
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
