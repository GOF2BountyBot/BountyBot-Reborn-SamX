from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

# Response Models
class GuildConfigResponse(BaseModel):
    guild_id: int
    configured: bool
    admin_role_configured: bool
    starting_credits: int
    sale_price_factor: float
    xp_thresholds: Dict[str, int]
    shop_config: Dict[str, Any]
    created_at: str
    updated_at: str

class ConfigValidationResponse(BaseModel):
    valid: bool
    errors: List[str]
    warnings: List[str]
    guild_id: int

class UpdateConfigRequest(BaseModel):
    guild_id: int
    admin_role_id: Optional[int] = None
    starting_credits: Optional[int] = Field(None, ge=0)
    sale_price_factor: Optional[float] = Field(None, gt=0, le=1)
    xp_thresholds: Optional[Dict[str, int]] = None

class UpdateShopConfigRequest(BaseModel):
    guild_id: int
    tech_level_probabilities: Optional[Dict[str, float]] = None
    item_count_ranges: Optional[Dict[str, Dict[str, int]]] = None
    quantity_ranges: Optional[Dict[str, Dict[str, int]]] = None

class UpdateXPThresholdsRequest(BaseModel):
    guild_id: int
    thresholds: Dict[str, int] = Field(
        ..., 
        description="XP thresholds for Silver, Gold, and Platinum tiers"
    )