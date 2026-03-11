from typing import List, Optional

from pydantic import BaseModel, Field


# Response Models
class ShipResponse(BaseModel):
    id: int
    player_id: int
    ship_name: str
    nickname: Optional[str]
    is_active: bool
    weapons: Optional[List[str]]
    modules: Optional[List[str]]
    turrets: Optional[List[str]]
    created_at: str

    class Config:
        from_attributes = True

class ShipLoadoutSummaryResponse(BaseModel):
    ship_id: int
    ship_name: str
    nickname: Optional[str]
    is_active: bool
    weapons: List[str]
    modules: List[str]
    turrets: List[str]
    weapons_count: int
    modules_count: int
    turrets_count: int

class CreateShipRequest(BaseModel):
    player_id: int
    ship_name: str
    nickname: Optional[str] = None
    weapons: Optional[List[str]] = []
    modules: Optional[List[str]] = []
    turrets: Optional[List[str]] = []

class UpdateLoadoutRequest(BaseModel):
    weapons: Optional[List[str]] = None
    modules: Optional[List[str]] = None
    turrets: Optional[List[str]] = None

class UpdateNicknameRequest(BaseModel):
    nickname: str

class EquipItemRequest(BaseModel):
    equipment_type: str = Field(pattern="^(weapons|modules|turrets)$")
    item_name: str

class UnequipItemRequest(BaseModel):
    equipment_type: str = Field(pattern="^(weapons|modules|turrets)$")
    item_name: str
