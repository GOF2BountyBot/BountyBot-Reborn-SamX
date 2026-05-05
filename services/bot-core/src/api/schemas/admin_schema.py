from typing import Literal

from pydantic import BaseModel, Field


# Request/Response Models
class InitializeGuildRequest(BaseModel):
    guild_id: int = Field(ge=1)
    admin_role_id: int | None = Field(default=None, ge=1)
    starting_credits: int = Field(default=0, ge=0)
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


class GuildInitializationResponse(BaseModel):
    guild_id: int
    admin_role_id: int | None
    shops_created: int
    config_created: bool
    channels_configured: bool = False
    bounty_hunter_role_id: int | None = None
    bronze_role_id: int | None = None
    silver_role_id: int | None = None
    gold_role_id: int | None = None
    platinum_role_id: int | None = None
    shop_announcements_role_id: int | None = None
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
    # A.45: concrete vocabulary only (includes ship for admin add/remove inventory paths).
    item_type: Literal["ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module"]
    item_name: str = Field(max_length=256)
    quantity: int = Field(gt=0, default=1)


class RemoveInventoryItemRequest(BaseModel):
    player_id: int = Field(ge=1)
    # A.45: concrete vocabulary only (includes ship for admin add/remove inventory paths).
    item_type: Literal["ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module"]
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


# Admin inventory management request/response schemas


class AdminGiveItemRequest(BaseModel):
    guild_id: int = Field(ge=1)
    user_id: int = Field(ge=1)
    item_name: str = Field(max_length=256)
    # B.80: item_type is now optional — the server resolves the concrete type
    # from the item catalog by name (same pattern as ShopService.sell_item A.42b).
    # When provided, it must be a valid 4-value concrete set (ship excluded).
    item_type: Literal["primary_weapon", "secondary_weapon", "turret_weapon", "module"] | None = None
    quantity: int = Field(gt=0, default=1)


class AdminRemoveItemRequest(BaseModel):
    guild_id: int = Field(ge=1)
    user_id: int = Field(ge=1)
    item_name: str = Field(max_length=256)
    # A.45 / B.80-style: item_type is now optional. When omitted, the server resolves
    # the concrete type from the player's inventory by item_name (same pattern as
    # AdminGiveItemRequest / ShopService.sell_item A.42b).
    # Ship is intentionally excluded; ships use AdminRemoveShipRequest.
    item_type: Literal["primary_weapon", "secondary_weapon", "turret_weapon", "module"] | None = None
    quantity: int = Field(gt=0, default=1)


class AdminGiveShipRequest(BaseModel):
    guild_id: int = Field(ge=1)
    user_id: int = Field(ge=1)
    ship_name: str = Field(max_length=256)


class AdminRemoveShipRequest(BaseModel):
    guild_id: int = Field(ge=1)
    user_id: int = Field(ge=1)
    ship_name: str = Field(max_length=256)


# Ship transfer schema (for player /give ship)


class TransferShipRequest(BaseModel):
    from_player_id: int = Field(ge=1)
    to_player_id: int = Field(ge=1)
    ship_id: int = Field(ge=1)
