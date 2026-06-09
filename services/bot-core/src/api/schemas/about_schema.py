from typing import Any

from pydantic import BaseModel, ConfigDict


# Pydantic models for responses
class ItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
    # §14 / T11: PrimaryWeaponMod breakdown fields
    # Sourced from extra_atts["extra_atts"]["damage_pct"], extra_atts["extra_atts"]["fire_rate_pct"],
    # and extra_atts["dpsMultiplier"] (camelCase at outer level in seed).
    damage_pct: int | None = None
    fire_rate_pct: int | None = None
    dps_multiplier: float | None = None  # camelCase seed key: "dpsMultiplier"


class WeaponResponse(ItemResponse):
    # §14 / T11: EMP damage field — applies to any weapon with emp_damage in inner extra_atts
    emp_damage: int | None = None


class PrimaryWeaponResponse(WeaponResponse):
    dps: float | None = None
    # D-002: per-shot breakdown fields sourced from inner extra_atts
    loading_speed_ms: int | None = None
    damage_per_shot: int | None = None
    subtype: str | None = None


class SecondaryWeaponResponse(WeaponResponse):
    # §14 / T11: cluster-missile and nuke fields
    burst_count: int | None = None  # cluster-missile sub-munition count
    nuke_direct_damage: int | None = None  # = damage when subtype == "nuke"
    nuke_effective_magnitude_m: int | None = None  # = magnitude_m * NUKE_MAGNITUDE_SCALE, rounded
    nuke_self_damage_factor: float | None = None  # = NUKE_FRIENDLY_FACTOR when subtype == "nuke"
    # D-004: weapon subtype for "Weapon type" embed field (e.g. "cluster-missile", "shock-blast")
    subtype: str | None = None


class TurretWeaponResponse(WeaponResponse):
    # D-002: per-shot breakdown fields sourced from inner extra_atts
    loading_speed_ms: int | None = None
    damage_per_shot: int | None = None
    subtype: str | None = None
    # D-003: firing mode — True=Automatic, False=Manual (None=omit)
    automatic: bool | None = None


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
