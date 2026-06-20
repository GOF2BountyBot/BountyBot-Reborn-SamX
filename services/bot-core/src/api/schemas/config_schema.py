from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GameConstantsOverridesMixin(BaseModel):
    """Mixin holding the per-guild game-constant override fields (B.49; extended over time).

    All fields are ``| None = None``.  NULL means "use the global GameConstants
    default" — the service layer resolves the actual value via ``resolve_constant``.
    """

    # Combat / Balance
    division_max_tl: dict[str, int] | None = None
    ship_value_reward_percentage: float | None = Field(None, ge=0.0, le=1.0)
    criminal_equip_damageless_weapon_chance: int | None = Field(None, ge=0, le=100)
    criminal_max_gear_upgrade: int | None = Field(None, ge=0, le=10)
    bounty_reward_to_xp_gain_mult: float | None = Field(None, ge=0.0)
    bounty_winner_reserve_factor: float | None = Field(None, ge=0.0, le=1.0)
    # bounty_pvc_armour_buff_factor retired T10 (replaced by pvc_damage_reduction §3)
    # duel_variance_percent retired T10 (SimpleTTKResolver removed)
    duel_cloak_chance: int | None = Field(None, ge=0, le=100)

    # Bounty mechanics
    close_bounty_threshold: int | None = Field(None, ge=1)
    max_route_length: int | None = Field(None, ge=1, le=500)
    min_route_systems: int | None = Field(None, ge=2, le=50)
    # ge=0: a max window of 0 disables the "recently spotted" hint guild-wide
    # (every bounty rolls B=0). Per-bounty B is rolled from [0, this value].
    recently_spotted_max_window: int | None = Field(None, ge=0, le=50)
    bounty_delay_random_min: int | None = Field(None, ge=0)
    bounty_delay_random_max: int | None = Field(None, ge=0)
    bounty_spawn_jitter: int | None = Field(None, ge=0)
    check_cooldown: int | None = Field(None, ge=0)
    duel_request_expiry: int | None = Field(None, ge=0)
    tier_change_cooldown: int | None = Field(None, ge=0)

    # Activity / Temperature
    guild_activity_decay_rate: float | None = Field(None, ge=0.0, le=1.0)
    min_guild_activity: float | None = Field(None, ge=0.0)
    activity_temp_per_player: int | None = Field(None, ge=0)

    # Shop
    shop_default_ships_num: int | None = Field(None, ge=0)
    shop_default_weapons_num: int | None = Field(None, ge=0)
    shop_default_modules_num: int | None = Field(None, ge=0)
    shop_default_turrets_num: int | None = Field(None, ge=0)
    turret_spawn_probability: int | None = Field(None, ge=0, le=100)

    # Inventory / Economy
    kaamo_max_capacity: int | None = Field(None, ge=0)
    classic_credits_per_check: int | None = Field(None, ge=0)

    # Demotion
    demotion_credit_penalty_pct: int | None = Field(None, ge=0, le=100)

    # Criminal loadout balance (BALANCE_JOURNAL §A — Thread 3 & 4)
    long_range_threshold_m: int | None = Field(None, ge=0)
    criminal_long_range_pct: float | None = Field(None, ge=0.0, le=1.0)
    primary_tl_band_weights: dict[str, int] | None = None
    criminal_cloak_chance_by_division: dict[str, int] | None = None
    criminal_booster_chance_by_division: dict[str, int] | None = None
    criminal_emergency_chance_by_division: dict[str, int] | None = None
    criminal_weaponmod_chance_by_division: dict[str, int] | None = None

    # Thread 6 — behavioral toggle (strict bool: reject 0/1/"true" coercion).
    criminal_exclude_emp_weapons: bool | None = Field(None, strict=True)

    # Loot (PvC) tunable knobs (LOOT_JOURNAL §8 / T2).
    # Chances + band-select are int-percent (0–100); qty/window are non-negative ints;
    # sell fraction is a non-negative float (default 1.0 = 100% face value).
    loot_chance_tractor_t1: int | None = Field(None, ge=0, le=100)
    loot_chance_tractor_t2: int | None = Field(None, ge=0, le=100)
    loot_chance_tractor_t3: int | None = Field(None, ge=0, le=100)
    loot_chance_tractor_t4: int | None = Field(None, ge=0, le=100)
    loot_chance_no_tractor: int | None = Field(None, ge=0, le=100)
    loot_band1_select_pct: int | None = Field(None, ge=0, le=100)
    loot_band2_select_pct: int | None = Field(None, ge=0, le=100)
    loot_band3_select_pct: int | None = Field(None, ge=0, le=100)
    loot_band1_tl_window: int | None = Field(None, ge=0)
    loot_band1_qty_min: int | None = Field(None, ge=0)
    loot_band1_qty_max: int | None = Field(None, ge=0)
    loot_band1_qty_mode: int | None = Field(None, ge=0)
    loot_band2_qty_min: int | None = Field(None, ge=0)
    loot_band2_qty_max: int | None = Field(None, ge=0)
    loot_band2_qty_mode: int | None = Field(None, ge=0)
    loot_band3_qty_min: int | None = Field(None, ge=0)
    loot_band3_qty_max: int | None = Field(None, ge=0)
    loot_band3_qty_mode: int | None = Field(None, ge=0)
    loot_commodity_sell_fraction: float | None = Field(None, ge=0.0)

    # Shop module-draw combat/filler split (NULL == GameConstants.SHOP_COMBAT_MODULE_PROB)
    shop_combat_module_prob: float | None = Field(None, ge=0.0, le=1.0)

    @field_validator("division_max_tl", mode="before")
    @classmethod
    def validate_division_max_tl(cls, v: Any) -> Any:
        if v is None:
            return v
        if not isinstance(v, dict):
            raise ValueError("division_max_tl must be a dict")
        required_keys = {"bronze", "silver", "gold", "platinum"}
        if set(v.keys()) != required_keys:
            raise ValueError(f"division_max_tl must have exactly keys: {required_keys}")
        for key, val in v.items():
            if not isinstance(val, int) or not 1 <= val <= 10:
                raise ValueError(f"division_max_tl[{key!r}] must be an integer between 1 and 10")
        return v

    @field_validator(
        "criminal_cloak_chance_by_division",
        "criminal_booster_chance_by_division",
        "criminal_emergency_chance_by_division",
        "criminal_weaponmod_chance_by_division",
        mode="before",
    )
    @classmethod
    def validate_criminal_division_chance(cls, v: Any, info: Any) -> Any:
        """Per-division equip-chance dict: keys exactly {bronze,silver,gold,platinum}, ints 0–100."""
        if v is None:
            return v
        name = info.field_name
        if not isinstance(v, dict):
            raise ValueError(f"{name} must be a dict")
        required_keys = {"bronze", "silver", "gold", "platinum"}
        if set(v.keys()) != required_keys:
            raise ValueError(f"{name} must have exactly keys: {required_keys}")
        for key, val in v.items():
            if isinstance(val, bool) or not isinstance(val, int) or not 0 <= val <= 100:
                raise ValueError(f"{name}[{key!r}] must be an integer between 0 and 100")
        return v

    @field_validator("primary_tl_band_weights", mode="before")
    @classmethod
    def validate_primary_tl_band_weights(cls, v: Any) -> Any:
        """TL-band weights: keys exactly {center,minus1,plus1}, non-negative ints."""
        if v is None:
            return v
        if not isinstance(v, dict):
            raise ValueError("primary_tl_band_weights must be a dict")
        required_keys = {"center", "minus1", "plus1"}
        if set(v.keys()) != required_keys:
            raise ValueError(f"primary_tl_band_weights must have exactly keys: {required_keys}")
        for key, val in v.items():
            if isinstance(val, bool) or not isinstance(val, int) or val < 0:
                raise ValueError(f"primary_tl_band_weights[{key!r}] must be a non-negative integer")
        return v

    @model_validator(mode="after")
    def validate_bounty_delay_range(self) -> "GameConstantsOverridesMixin":
        mn = self.bounty_delay_random_min
        mx = self.bounty_delay_random_max
        if mn is not None and mx is not None and mn > mx:
            raise ValueError("bounty_delay_random_min must be <= bounty_delay_random_max")
        return self


# Response Models
class GuildConfigResponse(GameConstantsOverridesMixin):
    model_config = ConfigDict(from_attributes=True)

    guild_id: int
    configured: bool
    admin_role_configured: bool
    starting_credits: int
    sale_price_factor: float
    xp_thresholds: dict[str, int]
    shop_config: dict[str, Any]
    created_at: str
    updated_at: str
    category_id: int | None = None
    shop_channel_id: int | None = None
    bronze_bounty_channel_id: int | None = None
    silver_bounty_channel_id: int | None = None
    gold_bounty_channel_id: int | None = None
    hunting_channel_id: int | None = None
    discussion_channel_id: int | None = None
    image_channel_id: int | None = None
    admin_role_id: int | None = None
    bounty_hunter_role_id: int | None = None
    bronze_role_id: int | None = None
    silver_role_id: int | None = None
    gold_role_id: int | None = None
    platinum_bounty_channel_id: int | None = None
    platinum_role_id: int | None = None
    shop_announcements_role_id: int | None = None


class ConfigValidationResponse(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str]
    guild_id: int


class UpdateConfigRequest(GameConstantsOverridesMixin):
    guild_id: int
    admin_role_id: int | None = None
    starting_credits: int | None = Field(None, ge=0)
    sale_price_factor: float | None = Field(None, gt=0, le=1)
    xp_thresholds: dict[str, int] | None = None
    category_id: int | None = None
    shop_channel_id: int | None = None
    bronze_bounty_channel_id: int | None = None
    silver_bounty_channel_id: int | None = None
    gold_bounty_channel_id: int | None = None
    hunting_channel_id: int | None = None
    discussion_channel_id: int | None = None
    image_channel_id: int | None = None
    bounty_hunter_role_id: int | None = None
    bronze_role_id: int | None = None
    silver_role_id: int | None = None
    gold_role_id: int | None = None
    platinum_bounty_channel_id: int | None = None
    platinum_role_id: int | None = None
    shop_announcements_role_id: int | None = None


class UpdateShopConfigRequest(BaseModel):
    guild_id: int
    tech_level_probabilities: dict[str, float] | None = None
    item_count_ranges: dict[str, dict[str, int]] | None = None
    quantity_ranges: dict[str, dict[str, int]] | None = None


class UpdateXPThresholdsRequest(BaseModel):
    guild_id: int
    thresholds: dict[str, int] = Field(..., description="XP thresholds for Silver, Gold, and Platinum tiers")


class UpdateBountyConfigRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    guild_id: int
    max_bounties_per_tier: dict[str, int] | None = None
    bounty_expiry_minutes: int | None = Field(None, ge=10, le=10080)
    bounty_spawn_interval_minutes: int | None = Field(None, ge=5, le=1440)


class BountyConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    guild_id: int
    max_bounties_per_tier: dict[str, int]
    bounty_expiry_minutes: int
    bounty_spawn_interval_minutes: int
    next_spawn_check_at: str | None = None


class BountyConfigStatusResponse(BountyConfigResponse):
    active_bounties_per_tier: dict[str, int]


class ResetGameConstantsRequest(BaseModel):
    fields: list[str] | None = None
