from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# Response Models
class GuildConfigResponse(BaseModel):
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


class UpdateConfigRequest(BaseModel):
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
