from typing import Any

from pydantic import BaseModel, Field


# Response Models
class InventoryItemResponse(BaseModel):
    id: int
    item_type: str
    item_name: str
    quantity: int
    acquired_at: str
    item_details: dict[str, Any]


class InventorySummaryResponse(BaseModel):
    player_id: int
    player_tier: str
    guild_id: int
    ship: int
    weapon: int
    module: int
    turret: int
    total_items: int


class AddItemRequest(BaseModel):
    player_id: int
    item_type: str = Field(pattern="^(ship|weapon|module|turret)$")
    item_name: str
    quantity: int = Field(gt=0, default=1)


class RemoveItemRequest(BaseModel):
    player_id: int
    item_type: str = Field(pattern="^(ship|weapon|module|turret)$")
    item_name: str
    quantity: int = Field(gt=0, default=1)


class TransferItemRequest(BaseModel):
    from_player_id: int
    to_player_id: int
    item_type: str = Field(pattern="^(ship|weapon|module|turret)$")
    item_name: str
    quantity: int = Field(gt=0, default=1)


class ItemTransactionResponse(BaseModel):
    player_id: int
    item_type: str
    item_name: str
    quantity_changed: int
    new_total_quantity: int
    transaction_time: str | None
