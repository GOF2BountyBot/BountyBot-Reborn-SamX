"""Add criminal-loadout balance override columns to guild_configs (BALANCE_JOURNAL §A).

Adds seven nullable per-guild override columns mirroring the B.49 NULL-means-default
pattern. NULL on any column means "use the matching GameConstants default" — the
service layer resolves the actual value via resolve_constant().

Columns (all nullable, default NULL):
- long_range_threshold_m              (Integer)  — Thread 3 primary range cutoff
- criminal_long_range_pct             (Float)    — Thread 3 long-range floor pct
- primary_tl_band_weights             (JSONB)    — Thread 3 ±1 TL-band weights
- criminal_cloak_chance_by_division   (JSONB)    — Thread 4 Cloak Gate-1 by division
- criminal_booster_chance_by_division (JSONB)    — Thread 4 Booster Gate-1 by division
- criminal_emergency_chance_by_division (JSONB)  — Thread 4 Emergency Gate-1 by division
- criminal_weaponmod_chance_by_division (JSONB)  — Thread 4 Weapon-Mod Gate-1 by division

Idempotent: guards every op with inspector checks so fresh-install DBs (where revision
0001 already materialised these columns from current ORM metadata) are no-ops, while
existing DBs receive the additive change. Mirrors the in-repo precedent of revision 0019.

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Portable JSON type: Postgres uses JSONB; SQLite test suite falls back to JSON.
_JSONB = sa.JSON().with_variant(JSONB(), "postgresql")

# (column_name, sqlalchemy_type) — order is the apply order; reversed for downgrade.
_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("long_range_threshold_m", sa.Integer()),
    ("criminal_long_range_pct", sa.Float()),
    ("primary_tl_band_weights", _JSONB),
    ("criminal_cloak_chance_by_division", _JSONB),
    ("criminal_booster_chance_by_division", _JSONB),
    ("criminal_emergency_chance_by_division", _JSONB),
    ("criminal_weaponmod_chance_by_division", _JSONB),
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
