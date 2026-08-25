from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GameConstantsOverridesMixin(BaseModel):
    """Mixin holding the per-guild game-constant override fields (B.49; extended over time).

    All fields are ``| None = None``.  NULL means "use the global GameConstants
    default" — the service layer resolves the actual value via ``resolve_constant``.

    Validation contract (issue #70): strict types — no cross-type coercion, so a
    bool field rejects ``0``/``1``/``"true"`` and an int field rejects ``"5"`` and
    ``true`` (int stays accepted for float fields as a lossless widening) — and
    every numeric field carries ge/le bounds so a fat-fingered override cannot
    wedge the game loop. Cross-field checks (delay min<=max, loot qty ordering)
    only fire when both sides arrive in the same request; a partial update is
    not cross-checked against values already stored.
    """

    model_config = ConfigDict(strict=True)

    # Combat / Balance
    division_max_tl: dict[str, int] | None = None
    ship_value_reward_percentage: float | None = Field(None, ge=0.0, le=1.0)
    criminal_equip_damageless_weapon_chance: int | None = Field(None, ge=0, le=100)
    criminal_max_gear_upgrade: int | None = Field(None, ge=0, le=10)
    bounty_reward_to_xp_gain_mult: float | None = Field(None, ge=0.0, le=100.0)
    bounty_winner_reserve_factor: float | None = Field(None, ge=0.0, le=1.0)
    # Per-division prize-pool scaler. NULL == GameConstants.BOUNTY_DIVISION_REWARD_MULT.
    bounty_division_reward_mult: dict[str, float] | None = None
    # bounty_pvc_armour_buff_factor retired T10 (replaced by pvc_damage_reduction §3)
    # duel_variance_percent retired T10 (SimpleTTKResolver removed)
    duel_cloak_chance: int | None = Field(None, ge=0, le=100)

    # Bounty mechanics
    close_bounty_threshold: int | None = Field(None, ge=1, le=50)
    max_route_length: int | None = Field(None, ge=1, le=500)
    min_route_systems: int | None = Field(None, ge=2, le=50)
    # ge=0: a max window of 0 disables the "recently spotted" hint guild-wide
    # (every bounty rolls B=0). Per-bounty B is rolled from [0, this value].
    recently_spotted_max_window: int | None = Field(None, ge=0, le=50)
    bounty_delay_random_min: int | None = Field(None, ge=0, le=1440)  # minutes; <= 1 day
    bounty_delay_random_max: int | None = Field(None, ge=0, le=1440)  # minutes; <= 1 day
    bounty_spawn_jitter: int | None = Field(None, ge=0, le=3600)  # seconds; <= 1 hour
    check_cooldown: int | None = Field(None, ge=0, le=86400)  # seconds; <= 1 day
    duel_request_expiry: int | None = Field(None, ge=0, le=2_592_000)  # seconds; <= 30 days
    tier_change_cooldown: int | None = Field(None, ge=0, le=2_592_000)  # seconds; <= 30 days

    # Activity / Temperature
    guild_activity_decay_rate: float | None = Field(None, ge=0.0, le=1.0)
    min_guild_activity: float | None = Field(None, ge=0.0, le=100.0)
    activity_temp_per_player: int | None = Field(None, ge=0, le=100)

    # Shop
    shop_default_ships_num: int | None = Field(None, ge=0, le=50)
    shop_default_weapons_num: int | None = Field(None, ge=0, le=50)
    shop_default_modules_num: int | None = Field(None, ge=0, le=50)
    shop_default_turrets_num: int | None = Field(None, ge=0, le=50)
    turret_spawn_probability: int | None = Field(None, ge=0, le=100)

    # Economy — kaamo_max_capacity retired (issue #70): Kaamo storage is not a mechanic
    classic_credits_per_check: int | None = Field(None, ge=0, le=1_000_000)

    # Demotion
    demotion_credit_penalty_pct: int | None = Field(None, ge=0, le=100)

    # Criminal loadout balance (BALANCE_JOURNAL §A — Thread 3 & 4)
    long_range_threshold_m: int | None = Field(None, ge=0, le=50_000)  # metres; battlefield is ~5 km
    criminal_long_range_pct: float | None = Field(None, ge=0.0, le=1.0)
    primary_tl_band_weights: dict[str, int] | None = None
    criminal_cloak_chance_by_division: dict[str, int] | None = None
    criminal_booster_chance_by_division: dict[str, int] | None = None
    criminal_emergency_chance_by_division: dict[str, int] | None = None
    criminal_weaponmod_chance_by_division: dict[str, int] | None = None

    # Thread 6 — behavioral toggle (strict bool now comes from the mixin-wide strict config).
    criminal_exclude_emp_weapons: bool | None = None

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
    loot_band1_tl_window: int | None = Field(None, ge=0, le=9)  # TL span is 1-10
    loot_band1_qty_min: int | None = Field(None, ge=0, le=1000)
    loot_band1_qty_max: int | None = Field(None, ge=0, le=1000)
    loot_band1_qty_mode: int | None = Field(None, ge=0, le=1000)
    loot_band2_qty_min: int | None = Field(None, ge=0, le=1000)
    loot_band2_qty_max: int | None = Field(None, ge=0, le=1000)
    loot_band2_qty_mode: int | None = Field(None, ge=0, le=1000)
    loot_band3_qty_min: int | None = Field(None, ge=0, le=1000)
    loot_band3_qty_max: int | None = Field(None, ge=0, le=1000)
    loot_band3_qty_mode: int | None = Field(None, ge=0, le=1000)
    loot_commodity_sell_fraction: float | None = Field(None, ge=0.0, le=10.0)  # 10x face value ceiling

    # Shop module-draw combat/filler split (NULL == GameConstants.SHOP_COMBAT_MODULE_PROB)
    shop_combat_module_prob: float | None = Field(None, ge=0.0, le=1.0)

    # ------------------------------------------------------------------
    # D-trivial + DIVISION_TL_CENTERS scalar overrides (issue #70, revision 0028)
    # NULL == "use the matching GameConstants default". resolve_constant() handles fallback.
    # ------------------------------------------------------------------

    # Criminal loadout — secondary weapon selection
    criminal_secondary_min_damage: int | None = Field(None, ge=0, le=1000)

    # Shop — secondary weapon quantity scalers
    shop_secondary_qty_scaler_heavy: int | None = Field(None, ge=1, le=50)
    shop_secondary_qty_scaler_standard: int | None = Field(None, ge=1, le=100)

    # Shop — per-tier in-band TL range bounds (1–10 each)
    shop_tl_band_lo_bronze: int | None = Field(None, ge=1, le=10)
    shop_tl_band_hi_bronze: int | None = Field(None, ge=1, le=10)
    shop_tl_band_lo_silver: int | None = Field(None, ge=1, le=10)
    shop_tl_band_hi_silver: int | None = Field(None, ge=1, le=10)
    shop_tl_band_lo_gold: int | None = Field(None, ge=1, le=10)
    shop_tl_band_hi_gold: int | None = Field(None, ge=1, le=10)
    shop_tl_band_lo_platinum: int | None = Field(None, ge=1, le=10)
    shop_tl_band_hi_platinum: int | None = Field(None, ge=1, le=10)

    # Shop — batch TL draw parameters
    shop_banded_tl_weight: float | None = Field(None, ge=0.0, le=1.0)
    shop_uptier_tl_decay: float | None = Field(None, ge=0.0, le=1.0)
    shop_downtier_tl_decay: float | None = Field(None, ge=0.0, le=1.0)

    # Division TL draw centres (per-guild scalars replacing DIVISION_TL_CENTERS dict)
    division_tl_center_bronze: int | None = Field(None, ge=1, le=10)
    division_tl_center_silver: int | None = Field(None, ge=1, le=10)
    division_tl_center_gold: int | None = Field(None, ge=1, le=10)
    division_tl_center_platinum: int | None = Field(None, ge=1, le=10)

    # ------------------------------------------------------------------
    # Previously column-only orphans — columns already existed; schema fields added here.
    # ------------------------------------------------------------------

    # Waypoint route generation (columns added in revision 0026)
    bounty_single_waypoint_prob: float | None = Field(None, ge=0.0, le=1.0)
    bounty_dual_waypoint_prob: float | None = Field(None, ge=0.0, le=1.0)
    bounty_waypoint_attempts: int | None = Field(None, ge=1, le=100)
    bounty_waypoint_min_degree: int | None = Field(None, ge=1, le=10)

    # PvC damage reduction — Keith T. Maxwell bonus (§3; column from Phase-1 schema)
    pvc_damage_reduction: float | None = Field(None, ge=0.0, le=1.0)

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

    @field_validator("bounty_division_reward_mult", mode="before")
    @classmethod
    def validate_bounty_division_reward_mult(cls, v: Any) -> Any:
        """Per-division pool scaler: keys exactly {bronze,silver,gold,platinum}, non-negative floats."""
        if v is None:
            return v
        if not isinstance(v, dict):
            raise ValueError("bounty_division_reward_mult must be a dict")
        required_keys = {"bronze", "silver", "gold", "platinum"}
        if set(v.keys()) != required_keys:
            raise ValueError(f"bounty_division_reward_mult must have exactly keys: {required_keys}")
        for key, val in v.items():
            if isinstance(val, bool) or not isinstance(val, (int, float)) or val < 0:
                raise ValueError(f"bounty_division_reward_mult[{key!r}] must be a non-negative number")
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

    @model_validator(mode="after")
    def validate_loot_qty_ordering(self) -> "GameConstantsOverridesMixin":
        """Loot qty triples are triangular-distribution params: min <= mode <= max.

        Checks every pair present in this request; values not in the request
        (partial update) cannot be cross-checked against the stored row.
        """
        for band in (1, 2, 3):
            mn = getattr(self, f"loot_band{band}_qty_min")
            mode = getattr(self, f"loot_band{band}_qty_mode")
            mx = getattr(self, f"loot_band{band}_qty_max")
            if mn is not None and mx is not None and mn > mx:
                raise ValueError(f"loot_band{band}_qty_min must be <= loot_band{band}_qty_max")
            if mn is not None and mode is not None and mode < mn:
                raise ValueError(f"loot_band{band}_qty_mode must be >= loot_band{band}_qty_min")
            if mx is not None and mode is not None and mode > mx:
                raise ValueError(f"loot_band{band}_qty_mode must be <= loot_band{band}_qty_max")
        return self

    @model_validator(mode="after")
    def validate_shop_tl_band_ordering(self) -> "GameConstantsOverridesMixin":
        """Per-tier shop TL band: lo must be <= hi when both are present in the request.

        Checks only pairs present in this request; a partial update supplying only
        one side cannot be cross-checked against the stored row (same pattern as
        validate_loot_qty_ordering and validate_bounty_delay_range).
        """
        for tier in ("bronze", "silver", "gold", "platinum"):
            lo = getattr(self, f"shop_tl_band_lo_{tier}")
            hi = getattr(self, f"shop_tl_band_hi_{tier}")
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(f"shop_tl_band_lo_{tier} must be <= shop_tl_band_hi_{tier}")
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
