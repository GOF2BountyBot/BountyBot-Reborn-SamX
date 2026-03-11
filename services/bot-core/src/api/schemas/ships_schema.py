
from pydantic import BaseModel, Field


# Response Models
class ShipResponse(BaseModel):
    id: int
    player_id: int
    ship_name: str
    nickname: str | None
    is_active: bool
    weapons: list[str] | None
    modules: list[str] | None
    turrets: list[str] | None
    created_at: str

    class Config:
        from_attributes = True

class ShipLoadoutSummaryResponse(BaseModel):
    ship_id: int
    ship_name: str
    nickname: str | None
    is_active: bool
    weapons: list[str]
    modules: list[str]
    turrets: list[str]
    weapons_count: int
    modules_count: int
    turrets_count: int

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
    equipment_type: str = Field(pattern="^(weapons|modules|turrets)$")
    item_name: str

class UnequipItemRequest(BaseModel):
    equipment_type: str = Field(pattern="^(weapons|modules|turrets)$")
    item_name: str
