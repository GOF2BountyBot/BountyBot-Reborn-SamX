"""Retire 22 dead per-guild override columns from guild_configs (issue #70, revision 0031).

This is Phase B of a two-phase cleanup (Phase A added flat scalars in 0030):
  - Drop 22 nullable guild_configs columns that are no longer referenced by any
    live code path (per the rev-0031 audit).
  - The "division_temperatures" JSONB column is dropped here too — the temperature
    subsystem was never fully wired and is being removed (owner-approved).

ENV-VAR RENAME (action required on deploy):
  BOUNTYBOT_BOUNTY_DELAY_RANDOM_MIN → BOUNTYBOT_BOUNTY_SPAWN_CHECK_INTERVAL_MINUTES

  The constant was renamed BOUNTY_DELAY_RANDOM_MIN → BOUNTY_SPAWN_CHECK_INTERVAL_MINUTES
  to reflect its true purpose: the spawn-orchestrator cron step in minutes, seeded
  into the persisted APScheduler store on first boot.  If you override this env var,
  update the key before deploying; the old key is silently ignored.
  To pick up the new cron interval without a full scheduler reset, run:
      POST /scheduler/reset

Columns DROPPED (all nullable):
  duel_cloak_chance                    — Integer
  criminal_equip_damageless_weapon_chance — Integer
  ship_value_reward_percentage         — Float
  shop_default_ships_num               — Integer
  shop_default_weapons_num             — Integer
  shop_default_modules_num             — Integer
  shop_default_turrets_num             — Integer
  turret_spawn_probability             — Integer
  guild_activity_decay_rate            — Float
  min_guild_activity                   — Float
  activity_temp_per_player             — Integer
  bounty_delay_random_min              — Integer
  bounty_delay_random_max              — Integer
  bounty_spawn_jitter                  — Integer
  tick_ms                              — Integer
  max_fight_ticks                      — Integer
  accuracy_clamp_min                   — Float
  accuracy_clamp_max                   — Float
  cloak_hp_thresholds_pct              — String
  booster_hp_thresholds_pct           — String
  combat_log_retention_hours           — Integer
  division_temperatures                — JSONB (temperature subsystem removed)

downgrade: guarded re-add of all 22 columns (nullable, original types).

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Portable JSON type: Postgres uses JSONB; SQLite unit-test suite falls back to JSON.
_JSONB = JSON().with_variant(JSONB(), "postgresql")

# (column_name, sqlalchemy_type) — 22 columns to drop.
_RETIRED_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("duel_cloak_chance", sa.Integer()),
    ("criminal_equip_damageless_weapon_chance", sa.Integer()),
    ("ship_value_reward_percentage", sa.Float()),
    ("shop_default_ships_num", sa.Integer()),
    ("shop_default_weapons_num", sa.Integer()),
    ("shop_default_modules_num", sa.Integer()),
    ("shop_default_turrets_num", sa.Integer()),
    ("turret_spawn_probability", sa.Integer()),
    ("guild_activity_decay_rate", sa.Float()),
    ("min_guild_activity", sa.Float()),
    ("activity_temp_per_player", sa.Integer()),
    ("bounty_delay_random_min", sa.Integer()),
    ("bounty_delay_random_max", sa.Integer()),
    ("bounty_spawn_jitter", sa.Integer()),
    ("tick_ms", sa.Integer()),
    ("max_fight_ticks", sa.Integer()),
    ("accuracy_clamp_min", sa.Float()),
    ("accuracy_clamp_max", sa.Float()),
    ("cloak_hp_thresholds_pct", sa.String()),
    ("booster_hp_thresholds_pct", sa.String()),
    ("combat_log_retention_hours", sa.Integer()),
    # Temperature subsystem (owner-approved removal; never fully wired).
    ("division_temperatures", _JSONB),
)


def _cols(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = _cols(inspector, "guild_configs")

    for col_name, _col_type in _RETIRED_COLUMNS:
        if col_name in existing:
            op.drop_column("guild_configs", col_name)


def downgrade() -> None:
    """Re-add all retired columns as nullable (original types)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = _cols(inspector, "guild_configs")

    for col_name, col_type in reversed(_RETIRED_COLUMNS):
        if col_name not in existing:
            op.add_column(
                "guild_configs",
                sa.Column(col_name, col_type, nullable=True),
            )
