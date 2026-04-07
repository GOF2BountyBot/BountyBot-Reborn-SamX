from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# Response Models
class PlayerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    guild_id: int
    credits: int
    lifetime_credits: int
    systems_checked: int
    bounty_wins: int
    xp: int
    tier: str
    prestige_count: int
    duel_wins: int
    duel_losses: int
    duel_credits_won: int
    duel_credits_lost: int
    active_ship_id: int | None
    xp_surplus: int = 0
    guild_transfer_cooldown: datetime | None = None
    classic_mode: bool = False
    bounty_cooldown_end: datetime | None = None
    created_at: str
    updated_at: str


class PlayerStatisticsResponse(BaseModel):
    player_id: int
    tier: str
    tier_level: int
    xp: int
    prestige_count: int
    credits: int
    lifetime_credits: int
    classic_mode: bool = False
    bounty_stats: dict[str, int]
    duel_stats: dict[str, Any]
    created_at: str
    updated_at: str


class CreatePlayerRequest(BaseModel):
    discord_id: int
    guild_id: int
    discord_username: str | None = None


class UpdateCreditsRequest(BaseModel):
    credits: int = Field(ge=0, description="Credits must be non-negative")
    update_lifetime: bool = Field(default=True, description="Whether to update lifetime credits")


class UpdateXPRequest(BaseModel):
    xp: int = Field(ge=0, le=1000000, description="XP must be between 0 and 1,000,000")


class UpdateTierRequest(BaseModel):
    tier: str = Field(
        pattern="^(Bronze|Silver|Gold|Platinum)$", description="Must be Bronze, Silver, Gold, or Platinum"
    )


class TransferCreditsRequest(BaseModel):
    source_player_id: int
    target_player_id: int
    amount: int = Field(gt=0)


class TransferCreditsResponse(BaseModel):
    source_player_id: int
    target_player_id: int
    amount: int
    source_remaining_credits: int
    target_new_credits: int


class PrestigeResponse(BaseModel):
    """Response returned after a successful prestige operation."""

    player_id: int
    prestige_count: int
    level_before: int
    division_before: str


class PromotionStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    player_id: int
    current_tier: str
    current_tier_level: int
    eligible_tier: str
    next_tier: str | None = None
    can_promote: bool
    xp: int
    xp_threshold_for_next: int | None = None
    xp_surplus_for_next: int | None = None


class PromoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    player_id: int
    old_tier: str
    new_tier: str
    xp: int
    eligible_for_next: bool
    next_tier: str | None = None


class LoadoutWeaponItem(BaseModel):
    name: str
    emoji: str | None = None
    dps: float | None = None
    value: int | None = None


class LoadoutModuleItem(BaseModel):
    name: str
    emoji: str | None = None
    type: str | None = None
    value: int | None = None
    tech_level: int | None = None


class PlayerLoadoutResponse(BaseModel):
    player_id: int
    ship_name: str | None = None
    ship_emoji: str | None = None
    ship_nickname: str | None = None
    armor_hp: int | None = None
    shield_hp: int | None = None
    total_hp: int | None = None
    total_dps: float | None = None
    weapons: list[LoadoutWeaponItem] = Field(default_factory=list)
    modules: list[LoadoutModuleItem] = Field(default_factory=list)
    turrets: list[LoadoutWeaponItem] = Field(default_factory=list)
    total_value: int | None = None
    message: str | None = None
