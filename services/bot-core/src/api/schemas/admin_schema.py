from pydantic import BaseModel, Field


# Request/Response Models
class InitializeGuildRequest(BaseModel):
    guild_id: int = Field(ge=1)
    admin_role_id: int | None = Field(default=None, ge=1)
    starting_credits: int = Field(default=0, ge=0)


class GuildInitializationResponse(BaseModel):
    guild_id: int
    admin_role_id: int | None
    shops_created: int
    config_created: bool
    message: str


class UpdatePlayerCreditsRequest(BaseModel):
    player_id: int = Field(ge=1)
    credits: int = Field(ge=0)
    update_lifetime: bool = True


class UpdatePlayerXPRequest(BaseModel):
    player_id: int = Field(ge=1)
    xp: int = Field(ge=0, le=1000000)


class AddInventoryItemRequest(BaseModel):
    player_id: int = Field(ge=1)
    item_type: str = Field(pattern="^(ship|weapon|module|turret)$")
    item_name: str = Field(max_length=256)
    quantity: int = Field(gt=0, default=1)


class RemoveInventoryItemRequest(BaseModel):
    player_id: int = Field(ge=1)
    item_type: str = Field(pattern="^(ship|weapon|module|turret)$")
    item_name: str = Field(max_length=256)
    quantity: int = Field(gt=0, default=1)


class RefreshShopRequest(BaseModel):
    guild_id: int = Field(ge=1)
    tier: str = Field(pattern="^(Bronze|Silver|Gold|Platinum)$")
    force_tech_level: int | None = Field(None, ge=1, le=9)


class UpdateShopConfigRequest(BaseModel):
    guild_id: int = Field(ge=1)
    tech_level_probabilities: dict[str, float] | None = None
    sale_price_factor: float | None = Field(None, gt=0, le=1)
    item_count_ranges: dict[str, dict[str, int]] | None = None
    quantity_ranges: dict[str, dict[str, int]] | None = None


class SystemHealthResponse(BaseModel):
    database_status: str
    total_users: int
    total_players: int
    total_guilds: int
    shop_items_count: int
    system_status: str
