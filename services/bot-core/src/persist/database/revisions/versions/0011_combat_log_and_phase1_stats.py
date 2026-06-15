"""Combat log table + Phase-1 schema additions.

Single revision covering all T2 deliverables:
  - Player lifetime counters: total_fights, total_nukes_fired, total_module_activations
  - PlayerShip.manual_turret_mode (dedicated Boolean column, §6.3 / §10)
  - combat_log table per §12 (guild_id, context, combatant names, user_ids,
    winner, is_stalemate, data JSON, created_at) + two single-column indexes
  - GuildConfig per-guild override columns for all 25 Appendix A constants

Idempotent: guards every op with inspector checks so fresh-install DBs (where
revision 0001 already materialized columns from current ORM metadata) are no-ops,
while existing prod DBs receive the additive changes.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _cols(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def _indexes(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {i["name"] for i in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ------------------------------------------------------------------ #
    # players — 3 lifetime counter columns (§13)                          #
    # ------------------------------------------------------------------ #
    player_cols = _cols(inspector, "players")
    for col_name in ("total_fights", "total_nukes_fired", "total_module_activations"):
        if col_name not in player_cols:
            op.add_column(
                "players",
                sa.Column(col_name, sa.Integer(), nullable=False, server_default="0"),
            )

    # ------------------------------------------------------------------ #
    # player_ships — manual_turret_mode (§6.3 / §10)                     #
    # ------------------------------------------------------------------ #
    ship_cols = _cols(inspector, "player_ships")
    if "manual_turret_mode" not in ship_cols:
        op.add_column(
            "player_ships",
            sa.Column("manual_turret_mode", sa.Boolean(), nullable=False, server_default="false"),
        )

    # ------------------------------------------------------------------ #
    # combat_log table + indexes (§12)                                    #
    # ------------------------------------------------------------------ #
    if not inspector.has_table("combat_log"):
        op.create_table(
            "combat_log",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("guild_id", sa.BigInteger(), nullable=False),
            sa.Column("context", sa.String(20), nullable=False),
            sa.Column("combatant1_name", sa.String(255), nullable=False),
            sa.Column("combatant2_name", sa.String(255), nullable=False),
            sa.Column("combatant1_user_id", sa.BigInteger(), nullable=True),
            sa.Column("combatant2_user_id", sa.BigInteger(), nullable=True),
            sa.Column("winner_name", sa.String(255), nullable=True),
            sa.Column("is_stalemate", sa.Boolean(), nullable=False),
            sa.Column("data", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        # Indexes are safe to create unconditionally here — table was just created.
        op.create_index("ix_combat_log_combatant1_user_id", "combat_log", ["combatant1_user_id"])
        op.create_index("ix_combat_log_combatant2_user_id", "combat_log", ["combatant2_user_id"])

    # ------------------------------------------------------------------ #
    # guild_configs — 25 Appendix A per-guild override columns            #
    # ------------------------------------------------------------------ #
    gc_cols = _cols(inspector, "guild_configs")

    _int_cols = [
        "scanner_tier_b_bonus_pp",
        "scanner_tier_c_bonus_pp",
        "tick_ms",
        "max_fight_ticks",
        "starting_distance_m",
        "base_ship_speed_mps",
        "min_distance_m",
        "thruster_window_m",
        "emergency_system_invuln_s",
        "combat_log_retention_hours",
    ]
    _float_cols = [
        "cloak_set_value",
        "booster_accuracy_debuff_factor",
        "thruster_accuracy_bonus_factor",
        "auto_turret_accuracy_multiplier",
        "player_base_accuracy",
        "npc_base_accuracy",
        "accuracy_clamp_min",
        "accuracy_clamp_max",
        "ketar_i_repair_pct_per_sec",
        "ketar_ii_repair_pct_per_sec",
        "nuke_magnitude_scale",
        "nuke_friendly_factor",
        "pvc_damage_reduction",
    ]
    _str_cols = [
        "cloak_hp_thresholds_pct",
        "booster_hp_thresholds_pct",
    ]

    for col_name in _int_cols:
        if col_name not in gc_cols:
            op.add_column("guild_configs", sa.Column(col_name, sa.Integer(), nullable=True))

    for col_name in _float_cols:
        if col_name not in gc_cols:
            op.add_column("guild_configs", sa.Column(col_name, sa.Float(), nullable=True))

    for col_name in _str_cols:
        if col_name not in gc_cols:
            op.add_column("guild_configs", sa.Column(col_name, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ------------------------------------------------------------------ #
    # guild_configs — drop Appendix A columns (reverse order, any that    #
    # exist)                                                               #
    # ------------------------------------------------------------------ #
    gc_cols = _cols(inspector, "guild_configs")
    for col_name in [
        "combat_log_retention_hours",
        "pvc_damage_reduction",
        "nuke_friendly_factor",
        "nuke_magnitude_scale",
        "emergency_system_invuln_s",
        "booster_hp_thresholds_pct",
        "cloak_hp_thresholds_pct",
        "thruster_window_m",
        "min_distance_m",
        "base_ship_speed_mps",
        "starting_distance_m",
        "max_fight_ticks",
        "tick_ms",
        "ketar_ii_repair_pct_per_sec",
        "ketar_i_repair_pct_per_sec",
        "accuracy_clamp_max",
        "accuracy_clamp_min",
        "npc_base_accuracy",
        "player_base_accuracy",
        "auto_turret_accuracy_multiplier",
        "thruster_accuracy_bonus_factor",
        "booster_accuracy_debuff_factor",
        "cloak_set_value",
        "scanner_tier_c_bonus_pp",
        "scanner_tier_b_bonus_pp",
    ]:
        if col_name in gc_cols:
            op.drop_column("guild_configs", col_name)

    # ------------------------------------------------------------------ #
    # combat_log — drop indexes then table                                 #
    # ------------------------------------------------------------------ #
    if inspector.has_table("combat_log"):
        existing_idx = _indexes(inspector, "combat_log")
        if "ix_combat_log_combatant2_user_id" in existing_idx:
            op.drop_index("ix_combat_log_combatant2_user_id", table_name="combat_log")
        if "ix_combat_log_combatant1_user_id" in existing_idx:
            op.drop_index("ix_combat_log_combatant1_user_id", table_name="combat_log")
        op.drop_table("combat_log")

    # ------------------------------------------------------------------ #
    # player_ships — drop manual_turret_mode                              #
    # ------------------------------------------------------------------ #
    ship_cols = _cols(inspector, "player_ships")
    if "manual_turret_mode" in ship_cols:
        op.drop_column("player_ships", "manual_turret_mode")

    # ------------------------------------------------------------------ #
    # players — drop lifetime counter columns                             #
    # ------------------------------------------------------------------ #
    player_cols = _cols(inspector, "players")
    for col_name in ("total_module_activations", "total_nukes_fired", "total_fights"):
        if col_name in player_cols:
            op.drop_column("players", col_name)
