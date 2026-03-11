from typing import Any, Dict, List, Optional

from pydantic import BaseModel


# Pydantic models for responses
class ItemResponse(BaseModel):
    id: int
    name: str
    aliases: List[str]
    built_in: bool
    emoji: Optional[str]
    icon: Optional[str]
    value: Optional[int]
    wiki: Optional[str]
    type: str
    tech_level: Optional[int] = None
    extra_atts: Optional[Dict[str, Any]] = None

class ModuleResponse(ItemResponse):
    max_equipped: Optional[int] = None

class WeaponResponse(ItemResponse):
    pass

class PrimaryWeaponResponse(WeaponResponse):
    dps: Optional[float] = None

class SecondaryWeaponResponse(WeaponResponse):
    pass

class TurretWeaponResponse(WeaponResponse):
    pass

class ShipResponse(ItemResponse):
    armour: Optional[int] = None
    cargo: Optional[int] = None
    handling: Optional[int] = None
    shop_spawn_rate: Optional[float] = None
    max_modules: Optional[int] = None
    max_primaries: Optional[int] = None
    max_secondaries: Optional[int] = None
    max_turrets: Optional[int] = None
    manufacturer: Optional[str] = None
    skinnable: Optional[bool] = None
    compatible_skins: Optional[Dict[str, str]] = None
    model: Optional[str] = None
    norm_spec: Optional[str] = None
    assets: Optional[List[str]] = None
    save_due: Optional[bool] = None

class CriminalResponse(ItemResponse):
    is_player: bool
    faction: str                   # ← add faction

class SystemResponse(ItemResponse):
    coordinates: List[float]       # ← e.g. [x, y, z]
    faction: str                   # ← add faction
