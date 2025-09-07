from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

# Request/Response Models
class InitializeGuildRequest(BaseModel):
    guild_id: int
    admin_role_id: Optional[int] = None
    starting_credits: int = Field(default=0, ge=0)

class GuildInitializationResponse(BaseModel):
    guild_id: int
    admin_role_id: Optional[int]
    shops_created: int
    config_created: bool
    message: str

class UpdatePlayerCreditsRequest(BaseModel):
    player_id: int
    credits: int = Field(ge=0)
    update_lifetime: bool = True

class UpdatePlayerXPRequest(BaseModel):
    player_id: int
    xp: int = Field(ge=0, le=1000000)

class AddInventoryItemRequest(BaseModel):
    player_id: int
    item_type: str = Field(pattern="^(ship|weapon|module|turret)$")
    item_name: str
    quantity: int = Field(gt=0, default=1)

class RemoveInventoryItemRequest(BaseModel):
    player_id: int
    item_type: str = Field(pattern="^(ship|weapon|module|turret)$")
    item_name: str
    quantity: int = Field(gt=0, default=1)

class RefreshShopRequest(BaseModel):
    guild_id: int
    tier: str = Field(pattern="^(Bronze|Silver|Gold|Platinum)$")
    force_tech_level: Optional[int] = Field(None, ge=1, le=9)

class UpdateShopConfigRequest(BaseModel):
    guild_id: int
    tech_level_probabilities: Optional[Dict[str, float]] = None
    sale_price_factor: Optional[float] = Field(None, gt=0, le=1)
    item_count_ranges: Optional[Dict[str, Dict[str, int]]] = None
    quantity_ranges: Optional[Dict[str, Dict[str, int]]] = None

class SystemHealthResponse(BaseModel):
    database_status: str
    total_users: int
    total_players: int
    total_guilds: int
    shop_items_count: int
    system_status: str