from pydantic import BaseModel, ConfigDict, Field


# Response Models
class ShipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    player_id: int
    ship_name: str
    nickname: str | None
    is_active: bool
    weapons: list[str] | None
    modules: list[str] | None
    turrets: list[str] | None
    secondary_weapons: list[str] | None = None
    created_at: str
    # Package G B.19: optional structured report from set_active_ship when
    # switching to a ship whose loadout exceeds its slot caps.  Pydantic
    # ignores unknown fields by default, so legacy consumers are unaffected.
    evacuated_items: dict[str, list[str]] | None = None
    any_evacuated: bool | None = None


class ShipLoadoutSummaryResponse(BaseModel):
    ship_id: int
    ship_name: str
    nickname: str | None
    is_active: bool
    weapons: list[str]
    modules: list[str]
    turrets: list[str]
    secondary_weapons: list[str] = []
    weapons_count: int
    modules_count: int
    turrets_count: int
    secondary_weapons_count: int = 0


class CreateShipRequest(BaseModel):
    player_id: int
    ship_name: str
    nickname: str | None = None
    weapons: list[str] | None = []
    modules: list[str] | None = []
    turrets: list[str] | None = []


class UpdateLoadoutRequest(BaseModel):
    weapons: list[str] | None = None
    modules: list[str] | None = None
    turrets: list[str] | None = None


class UpdateNicknameRequest(BaseModel):
    nickname: str


class EquipItemRequest(BaseModel):
    player_id: int
    # Equipment slot category (NOT item_type). Permitted: weapons | modules | turrets.
    # This is orthogonal to item_type vocabulary — e.g. slot "weapons" holds "primary_weapon" items.
    equipment_type: str | None = Field(default=None, pattern="^(weapons|modules|turrets)$")
    item_name: str


class UnequipItemRequest(BaseModel):
    player_id: int
    # Equipment slot category (NOT item_type). Permitted: weapons | modules | turrets.
    # This is orthogonal to item_type vocabulary — e.g. slot "weapons" holds "primary_weapon" items.
    equipment_type: str | None = Field(default=None, pattern="^(weapons|modules|turrets)$")
    item_name: str


class EquipCheckRequest(BaseModel):
    player_id: int
    item_name: str


class EquipCheckResponse(BaseModel):
    status: str  # "ok", "slot_full", "unique_conflict"
    equipment_type: str | None = None
    item_type: str | None = None
    module_class: str | None = None
    max_equipped: int | None = None
    max_slots: int | None = None
    equipped_items: list[dict] | None = None
    conflicting_item: dict | None = None


class TransferShipRequest(BaseModel):
    from_player_id: int = Field(ge=1)
    to_player_id: int = Field(ge=1)
    ship_id: int = Field(ge=1)


class TransferShipResponse(BaseModel):
    ship_id: int
    ship_name: str
    from_player_id: int
    to_player_id: int
    items_returned_to_source: list[str]
    message: str
