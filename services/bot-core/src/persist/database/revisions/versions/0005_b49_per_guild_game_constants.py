"""B.49: Add per-guild game-constant override columns to guild_configs.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-09

Adds 25 nullable columns to ``guild_configs`` for per-guild overrides of
operational GameConstants. NULL means "use the global GameConstants default".
All-nullable, idempotent column-existence guards. Safe to run on fresh installs.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None

_NEW_COLUMNS: list[tuple[str, sa.types.TypeEngine]] = [
    # Combat / Balance
    ("division_max_tl", sa.JSON()),
    ("ship_value_reward_percentage", sa.Float()),
    ("criminal_equip_damageless_weapon_chance", sa.Integer()),
    ("criminal_max_gear_upgrade", sa.Integer()),
    ("bounty_reward_to_xp_gain_mult", sa.Float()),
    ("bounty_winner_reserve_factor", sa.Float()),
    ("bounty_pvc_armour_buff_factor", sa.Float()),
    ("duel_variance_percent", sa.Float()),
    ("duel_cloak_chance", sa.Integer()),
    # Bounty mechanics
    ("close_bounty_threshold", sa.Integer()),
    ("max_route_length", sa.Integer()),
    ("bounty_delay_random_min", sa.Integer()),
    ("bounty_delay_random_max", sa.Integer()),
    ("bounty_spawn_jitter", sa.Integer()),
    ("check_cooldown", sa.Integer()),
    ("duel_request_expiry", sa.Integer()),
    # Activity / Temperature
    ("guild_activity_decay_rate", sa.Float()),
    ("min_guild_activity", sa.Float()),
    ("activity_temp_per_player", sa.Integer()),
    # Shop
    ("shop_default_ships_num", sa.Integer()),
    ("shop_default_weapons_num", sa.Integer()),
    ("shop_default_modules_num", sa.Integer()),
    ("shop_default_turrets_num", sa.Integer()),
    ("turret_spawn_probability", sa.Integer()),
    # Inventory / Economy
    ("kaamo_max_capacity", sa.Integer()),
    ("classic_credits_per_check", sa.Integer()),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns("guild_configs")}
    for name, col_type in _NEW_COLUMNS:
        if name not in existing:
            op.add_column("guild_configs", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns("guild_configs")}
    for name, _col_type in reversed(_NEW_COLUMNS):
        if name in existing:
            op.drop_column("guild_configs", name)
