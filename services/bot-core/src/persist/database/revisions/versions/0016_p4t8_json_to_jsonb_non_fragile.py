"""P4-T8: Alter JSON → JSONB for non-fragile columns.

Converts the following columns from JSON to JSONB:
  combat_log.data
  guild_configs: ship_count_range, weapon_count_range, secondary_weapon_count_range,
    module_count_range, turret_count_range, ship_quantity_range, weapon_quantity_range,
    secondary_weapon_quantity_range, module_quantity_range, turret_quantity_range,
    tech_level_probabilities, xp_thresholds, division_temperatures, bounty_max_per_tier,
    division_max_tl
  bounty: route, checked, criminal_ship
  ship.extra_atts
  commodity.extra_atts
  weapon.extra_atts
  module.extra_atts

Excluded per P4-T3 audit:
  system.connections — NOT a JSON column (PostgreSQL ARRAY(String), not JSON/JSONB)
  player_ship.* — fragile subsystem, deferred to P4-T9
  ship.compatible_skins — not in P4-T8 scope

Reversible: downgrade() restores all columns to JSON.
No JSONB-only indexes are added (no index drop needed in downgrade).

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | None = None
depends_on: str | None = None

# ---------------------------------------------------------------------------
# Column specifications: (table, column_name)
# ---------------------------------------------------------------------------

_COMBAT_LOG_COLS = [
    ("combat_log", "data"),
]

_GUILD_CONFIG_COLS = [
    ("guild_configs", "ship_count_range"),
    ("guild_configs", "weapon_count_range"),
    ("guild_configs", "secondary_weapon_count_range"),
    ("guild_configs", "module_count_range"),
    ("guild_configs", "turret_count_range"),
    ("guild_configs", "ship_quantity_range"),
    ("guild_configs", "weapon_quantity_range"),
    ("guild_configs", "secondary_weapon_quantity_range"),
    ("guild_configs", "module_quantity_range"),
    ("guild_configs", "turret_quantity_range"),
    ("guild_configs", "tech_level_probabilities"),
    ("guild_configs", "xp_thresholds"),
    ("guild_configs", "division_temperatures"),
    ("guild_configs", "bounty_max_per_tier"),
    ("guild_configs", "division_max_tl"),
]

_BOUNTY_COLS = [
    ("bounty", "route"),
    ("bounty", "checked"),
    ("bounty", "criminal_ship"),
]

_ITEM_EXTRA_ATTS_COLS = [
    ("ship", "extra_atts"),
    ("commodity", "extra_atts"),
    ("weapon", "extra_atts"),
    ("module", "extra_atts"),
]

_ALL_COLS = _COMBAT_LOG_COLS + _GUILD_CONFIG_COLS + _BOUNTY_COLS + _ITEM_EXTRA_ATTS_COLS

_JSONB_TYPE = JSONB()
_JSON_TYPE = sa.JSON()


def upgrade() -> None:
    """Alter all non-fragile JSON columns to JSONB."""
    for table, column in _ALL_COLS:
        op.alter_column(
            table,
            column,
            type_=_JSONB_TYPE,
            postgresql_using=f"{column}::jsonb",
        )


def downgrade() -> None:
    """Restore all JSONB columns back to JSON."""
    for table, column in _ALL_COLS:
        op.alter_column(
            table,
            column,
            type_=_JSON_TYPE,
            postgresql_using=f"{column}::json",
        )
