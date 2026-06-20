from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# Response Models
class InventoryItemResponse(BaseModel):
    id: int
    item_type: str
    item_name: str
    quantity: int
    acquired_at: str
    # Nullable so a single unresolved item (e.g. a catalog row gone missing) can
    # never reject the WHOLE inventory list. Normal items resolve to a detail
    # dict; commodities resolve via the commodity repo (see
    # InventoryService._get_items_details_batch).
    item_details: dict[str, Any] | None = None


class InventorySummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    player_id: int
    player_tier: str
    guild_id: int
    ship: int
    primary_weapon: int
    secondary_weapon: int
    turret_weapon: int
    module: int
    total_items: int


# A.45: item_type fields now use Literal instead of Field(pattern=...).
# Concrete vocabulary only — aliases ("weapon", "turret") are rejected at 422.
class AddItemRequest(BaseModel):
    player_id: int
    item_type: Literal["ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module"]
    item_name: str
    quantity: int = Field(gt=0, default=1)


class RemoveItemRequest(BaseModel):
    player_id: int
    item_type: Literal["ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module"]
    item_name: str
    quantity: int = Field(gt=0, default=1)


class TransferItemRequest(BaseModel):
    from_player_id: int
    to_player_id: int
    # C-1 (PvC loot): "commodity" is a first-class transferable type so a commodity
    # /give passes schema validation instead of 422-ing before reaching the service.
    item_type: Literal["ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module", "commodity"]
    item_name: str
    quantity: int = Field(gt=0, default=1)


class ItemTransactionResponse(BaseModel):
    player_id: int
    item_type: str
    item_name: str
    quantity_changed: int
    new_total_quantity: int
    transaction_time: str | None
