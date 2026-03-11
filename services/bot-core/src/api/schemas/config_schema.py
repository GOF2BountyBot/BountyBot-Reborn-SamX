from typing import Any

from pydantic import BaseModel, Field


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

class UpdateShopConfigRequest(BaseModel):
    guild_id: int
    tech_level_probabilities: dict[str, float] | None = None
    item_count_ranges: dict[str, dict[str, int]] | None = None
    quantity_ranges: dict[str, dict[str, int]] | None = None

class UpdateXPThresholdsRequest(BaseModel):
    guild_id: int
    thresholds: dict[str, int] = Field(
        ...,
        description="XP thresholds for Silver, Gold, and Platinum tiers"
    )
