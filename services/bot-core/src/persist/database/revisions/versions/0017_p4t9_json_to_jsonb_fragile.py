"""P4-T9: Alter JSON → JSONB for fragile loadout/inventory columns.

Converts the following player_ships columns from JSON to JSONB:
  player_ships.weapons
  player_ships.modules
  player_ships.turrets
  player_ships.secondary_weapons
  player_ships.secondary_ammo

Excluded columns (verified by reading the models):
  player_inventory — the PlayerInventory model has NO JSON columns
    (Integer, String, DateTime, UniqueConstraint only; nothing to convert).

Excluded per P4-T3 audit / T8 header (already done or out-of-scope):
  All non-fragile JSON cols (combat_log, guild_configs, bounty, ship/weapon/
  commodity/module extra_atts) — done in 0016 (P4-T8).

The PlayerShip model uses JSON().with_variant(JSONB(), "postgresql") so that:
  - PostgreSQL gets native JSONB (binary storage, sub-path operators, GIN indexable)
  - SQLite unit-test suite continues to create tables and round-trip values
    without a "JSONB is unsupported" error.

Reversible: downgrade() restores all columns to JSON.
No JSONB-only indexes are added (no index drop needed in downgrade).

This migration is sequenced AFTER D5 locking (P2.5/D5 done) so the fragile
equip/unequip/sell/shop/switch-ship paths are already serialised at the
PlayerShip aggregate-root level before the storage type changes.

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | None = None
depends_on: str | None = None

# ---------------------------------------------------------------------------
# Column specifications: (table, column_name)
# ---------------------------------------------------------------------------

# player_ships: all five JSON columns on PlayerShip
_PLAYER_SHIP_COLS = [
    ("player_ships", "weapons"),
    ("player_ships", "modules"),
    ("player_ships", "turrets"),
    ("player_ships", "secondary_weapons"),
    ("player_ships", "secondary_ammo"),
]

# NOTE: player_inventories has NO JSON columns — Integer/String/DateTime only.
# Nothing to migrate there.

_ALL_COLS = _PLAYER_SHIP_COLS

_JSONB_TYPE = JSONB()
_JSON_TYPE = sa.JSON()


def upgrade() -> None:
    """Alter all fragile player_ships JSON columns to JSONB."""
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
