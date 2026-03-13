from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# Response Models
class PlayerResponse(BaseModel):
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

    class Config:
        from_attributes = True

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
        pattern="^(Bronze|Silver|Gold|Platinum)$",
        description="Must be Bronze, Silver, Gold, or Platinum"
    )
