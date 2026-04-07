from pydantic import BaseModel, Field


# Response Models
class ShopItemResponse(BaseModel):
    id: int
    guild_id: int
    tier: str
    tech_level: int
    item_type: str
    item_name: str
    quantity: int
    price: int
    last_restocked: str
    refresh_interval_hours: int
    emoji: str | None = None


class ShopSummaryResponse(BaseModel):
    guild_id: int
    total_items: int
    shops: dict[str, dict[str, int]]


class PurchaseRequest(BaseModel):
    player_id: int
    shop_item_id: int
    quantity: int = Field(gt=0, default=1)


class SellRequest(BaseModel):
    player_id: int
    item_type: str = Field(pattern="^(ship|weapon|module|turret)$")
    item_name: str
    quantity: int = Field(gt=0, default=1)
    target_tier: str = Field(default="Bronze", pattern="^(Bronze|Silver|Gold|Platinum)$")


class TransactionResponse(BaseModel):
    player_id: int
    item_type: str
    item_name: str
    quantity: int
    total_cost: int | None = None
    total_value: int | None = None
    remaining_credits: int
    transaction_type: str


class RefreshShopRequest(BaseModel):
    guild_id: int
    tier: str = Field(pattern="^(Bronze|Silver|Gold|Platinum)$")
    force_tech_level: int | None = Field(None, ge=1, le=9)


class ShipPurchaseRequest(BaseModel):
    player_id: int
    shop_item_id: int
    sell_old_ship: bool = False


class ShipSellRequest(BaseModel):
    player_id: int
    ship_id: int
    clear_equipment: bool = False
    target_tier: str = Field(default="Bronze", pattern="^(Bronze|Silver|Gold|Platinum)$")
