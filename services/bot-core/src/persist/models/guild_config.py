"""
GuildConfig model for the BountyBot inventory system.

Stores all configurable parameters for each guild including shop settings,
economic factors, progression thresholds, and administrative settings.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, DateTime, Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Portable JSON type: Postgres uses JSONB; SQLite unit-test suite falls back to JSON.
_JSONB = JSON().with_variant(JSONB(), "postgresql")

from persist.database.tablenames import TableNames
from persist.models.base import Base


class GuildConfig(Base):
    __tablename__ = TableNames.GuildConfigs.value

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)

    # Admin role configuration
    admin_role_id: Mapped[int] = mapped_column(BigInteger, nullable=True)

    # Discord channel IDs for announcements
    category_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    shop_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bronze_bounty_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    silver_bounty_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    gold_bounty_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    hunting_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    discussion_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    image_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bounty_hunter_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bronze_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    silver_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    gold_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    platinum_bounty_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    platinum_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    shop_announcements_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Shop inventory size ranges (JSON objects with min/max values)
    ship_count_range: Mapped[dict[str, int]] = mapped_column(_JSONB, default={"min": 3, "max": 5})
    weapon_count_range: Mapped[dict[str, int]] = mapped_column(_JSONB, default={"min": 3, "max": 5})
    secondary_weapon_count_range: Mapped[dict[str, int]] = mapped_column(_JSONB, default={"min": 3, "max": 5})
    module_count_range: Mapped[dict[str, int]] = mapped_column(_JSONB, default={"min": 3, "max": 5})
    turret_count_range: Mapped[dict[str, int]] = mapped_column(_JSONB, default={"min": 3, "max": 5})

    # Quantity ranges for each item type
    ship_quantity_range: Mapped[dict[str, int]] = mapped_column(_JSONB, default={"min": 1, "max": 1})
    weapon_quantity_range: Mapped[dict[str, int]] = mapped_column(_JSONB, default={"min": 2, "max": 4})
    secondary_weapon_quantity_range: Mapped[dict[str, int]] = mapped_column(_JSONB, default={"min": 2, "max": 4})
    module_quantity_range: Mapped[dict[str, int]] = mapped_column(_JSONB, default={"min": 2, "max": 4})
    turret_quantity_range: Mapped[dict[str, int]] = mapped_column(_JSONB, default={"min": 2, "max": 4})

    # Tech level probabilities (JSON objects)
    tech_level_probabilities: Mapped[dict[str, float]] = mapped_column(
        _JSONB, default={"same_level": 0.70, "one_lower": 0.20, "two_lower": 0.10}
    )

    # Economic settings
    sale_price_factor: Mapped[float] = mapped_column(Float, default=0.8)
    starting_credits: Mapped[int] = mapped_column(Integer, default=0)

    # XP and tier thresholds
    xp_thresholds: Mapped[dict[str, int]] = mapped_column(
        _JSONB, default={"Silver": 1000, "Gold": 5000, "Platinum": 15000}
    )

    # Activity temperature per division (persisted for decay across restarts)
    # Default: {"bronze": 1.0, "silver": 1.0, "gold": 1.0, "platinum": 1.0}
    division_temperatures: Mapped[dict[str, float]] = mapped_column(
        _JSONB,
        default={"bronze": 1.0, "silver": 1.0, "gold": 1.0, "platinum": 1.0},
        nullable=True,
    )

    # Bounty configuration (per-guild)
    bounty_max_per_tier: Mapped[dict[str, int] | None] = mapped_column(
        _JSONB, default={"bronze": 3, "silver": 3, "gold": 3, "platinum": 3}, nullable=True
    )
    bounty_expiry_minutes: Mapped[int | None] = mapped_column(Integer, default=480, nullable=True)
    bounty_spawn_interval_minutes: Mapped[int | None] = mapped_column(Integer, default=60, nullable=True)
    next_spawn_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ------------------------------------------------------------------
    # B.49: Per-guild game-balance overrides
    # NULL means "use the global GameConstants default" (fallback in service layer)
    # ------------------------------------------------------------------

    # Combat / Balance
    division_max_tl: Mapped[dict[str, int] | None] = mapped_column(_JSONB, nullable=True, default=None)
    ship_value_reward_percentage: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    criminal_equip_damageless_weapon_chance: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    criminal_max_gear_upgrade: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    bounty_reward_to_xp_gain_mult: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    bounty_winner_reserve_factor: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    # bounty_pvc_armour_buff_factor — retired T10 (replaced by pvc_damage_reduction §3)
    # duel_variance_percent — retired T10 (SimpleTTKResolver removed)
    duel_cloak_chance: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    # Bounty mechanics
    close_bounty_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    max_route_length: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    bounty_delay_random_min: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    bounty_delay_random_max: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    bounty_spawn_jitter: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    check_cooldown: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    duel_request_expiry: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    tier_change_cooldown: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    # Activity / Temperature
    guild_activity_decay_rate: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    min_guild_activity: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    activity_temp_per_player: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    # Shop
    shop_default_ships_num: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    shop_default_weapons_num: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    shop_default_modules_num: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    shop_default_turrets_num: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    turret_spawn_probability: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    # Inventory / Economy
    kaamo_max_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    classic_credits_per_check: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    # Demotion — NULL means "use GameConstants.DEMOTION_CREDIT_PENALTY_PCT (10)"
    demotion_credit_penalty_pct: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    # ------------------------------------------------------------------
    # Criminal loadout balance overrides (BALANCE_JOURNAL §A — Thread 3 & 4)
    # NULL == "use the matching GameConstants default". resolve_constant() handles fallback.
    # ------------------------------------------------------------------

    # Thread 3 — primary long-range selection
    long_range_threshold_m: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    criminal_long_range_pct: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    primary_tl_band_weights: Mapped[dict[str, int] | None] = mapped_column(_JSONB, nullable=True, default=None)

    # Thread 4 — criminal two-gate module Gate-1 equip chances by division (%)
    criminal_cloak_chance_by_division: Mapped[dict[str, int] | None] = mapped_column(
        _JSONB, nullable=True, default=None
    )
    criminal_booster_chance_by_division: Mapped[dict[str, int] | None] = mapped_column(
        _JSONB, nullable=True, default=None
    )
    criminal_emergency_chance_by_division: Mapped[dict[str, int] | None] = mapped_column(
        _JSONB, nullable=True, default=None
    )
    criminal_weaponmod_chance_by_division: Mapped[dict[str, int] | None] = mapped_column(
        _JSONB, nullable=True, default=None
    )

    # ------------------------------------------------------------------
    # Combat System — Phase-1 per-guild overrides (Appendix A constants)
    # NULL == "use GameConstants default". resolve_constant() handles fallback.
    # ------------------------------------------------------------------

    # Accuracy system (§5)
    cloak_set_value: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    booster_accuracy_debuff_factor: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    thruster_accuracy_bonus_factor: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    auto_turret_accuracy_multiplier: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    player_base_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    npc_base_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    accuracy_clamp_min: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    accuracy_clamp_max: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    scanner_tier_b_bonus_pp: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    scanner_tier_c_bonus_pp: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    # Repair bots (§3 / §7.6)
    ketar_i_repair_pct_per_sec: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    ketar_ii_repair_pct_per_sec: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)

    # Tick / timing (§1)
    tick_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    max_fight_ticks: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    # Distance model (§2)
    starting_distance_m: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    base_ship_speed_mps: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    min_distance_m: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    thruster_window_m: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    # HP-threshold activation lists (§7.2 / §7.3 / §8) — stored comma-separated
    cloak_hp_thresholds_pct: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    booster_hp_thresholds_pct: Mapped[str | None] = mapped_column(String, nullable=True, default=None)

    # EmergencySystem (§7.7)
    emergency_system_invuln_s: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    # Nuke (§6.2)
    nuke_magnitude_scale: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    nuke_friendly_factor: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)

    # PvC damage reduction — Keith T. Maxwell bonus (§3)
    pvc_damage_reduction: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)

    # Combat log retention (§12)
    combat_log_retention_hours: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    # Relationships
    # B.31a: cascade="all, delete-orphan" ensures SQLAlchemy issues DELETE (not SET NULL)
    # for related GuildShop rows when the parent GuildConfig is deleted.  Without this,
    # SQLAlchemy emits UPDATE guild_shops SET guild_id=NULL which PostgreSQL rejects
    # (guild_id is NOT NULL) — causing a 500 on POST /config/guild/{id}/reset.
    shops: Mapped[list["GuildShop"]] = relationship(
        "GuildShop", back_populates="guild_config", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<GuildConfig(guild_id={self.guild_id}, starting_credits={self.starting_credits})>"

    def get_tier_threshold(self, tier: str) -> int:
        """Get XP threshold for a specific tier."""
        return self.xp_thresholds.get(tier, 0)

    def get_count_range(self, item_type: str) -> dict[str, int]:
        """Get item count range for shop generation."""
        range_map = {
            "ship": self.ship_count_range,
            "weapon": self.weapon_count_range,
            "secondary_weapon": self.secondary_weapon_count_range,
            "module": self.module_count_range,
            "turret": self.turret_count_range,
        }
        return range_map.get(item_type, {"min": 1, "max": 1})

    def get_quantity_range(self, item_type: str) -> dict[str, int]:
        """Get quantity range for shop items of a specific type."""
        range_map = {
            "ship": self.ship_quantity_range,
            "weapon": self.weapon_quantity_range,
            "secondary_weapon": self.secondary_weapon_quantity_range,
            "module": self.module_quantity_range,
            "turret": self.turret_quantity_range,
        }
        return range_map.get(item_type, {"min": 1, "max": 1})
