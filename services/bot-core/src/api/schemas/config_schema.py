from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GameConstantsOverridesMixin(BaseModel):
    """Mixin holding the 25 per-guild game-constant override fields (B.49).

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
    bounty_pvc_armour_buff_factor: float | None = Field(None, ge=0.0)
    duel_variance_percent: float | None = Field(None, ge=0.0, le=1.0)
    duel_cloak_chance: int | None = Field(None, ge=0, le=100)

    # Bounty mechanics
    close_bounty_threshold: int | None = Field(None, ge=1)
    max_route_length: int | None = Field(None, ge=1, le=500)
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
            if not isinstance(val, int) or not (1 <= val <= 10):
                raise ValueError(f"division_max_tl[{key!r}] must be an integer between 1 and 10")
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
