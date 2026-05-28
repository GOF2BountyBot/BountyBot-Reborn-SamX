from typing import Any

from pydantic import BaseModel, ConfigDict


# Pydantic models for responses
class ItemResponse(BaseModel):
    id: int
    name: str
    aliases: list[str]
    built_in: bool
    emoji: str | None
    icon: str | None
    value: int | None
    wiki: str | None
    type: str
    tech_level: int | None = None
    extra_atts: dict[str, Any] | None = None


class ModuleResponse(ItemResponse):
    max_equipped: int | None = None


class WeaponResponse(ItemResponse):
    pass


class PrimaryWeaponResponse(WeaponResponse):
    dps: float | None = None


class SecondaryWeaponResponse(WeaponResponse):
    pass


class TurretWeaponResponse(WeaponResponse):
    pass


class ShipResponse(ItemResponse):
    armour: int | None = None
    cargo: int | None = None
    handling: int | None = None
    shop_spawn_rate: float | None = None
    max_modules: int | None = None
    max_primaries: int | None = None
    max_secondaries: int | None = None
    max_turrets: int | None = None
    manufacturer: str | None = None
    skinnable: bool | None = None
    compatible_skins: dict[str, str] | None = None
    model: str | None = None
    norm_spec: str | None = None
    assets: list[str] | None = None
    save_due: bool | None = None


class CriminalResponse(ItemResponse):
    is_player: bool
    faction: str  # ← add faction


class SystemResponse(ItemResponse):
    coordinates: list[float]  # ← e.g. [x, y, z]
    faction: str  # ← add faction


class CommodityResponse(ItemResponse):
    model_config = ConfigDict(from_attributes=True)

    subcategory: str
    price_source: str | None = None
    price_range_min_credits: int | None = None
    price_range_max_credits: int | None = None
    price_range_min_system: str | None = None
    price_range_max_system: str | None = None
    highest_non_loma_price: int | None = None
    highest_non_loma_system: str | None = None
