from enum import Enum
from typing import List, Optional, Dict, Any, AsyncGenerator
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from persist.database.manager import db_manager
from persist.repositories.module_repository import ModuleRepository
from persist.repositories.primary_weapon_repository import PrimaryWeaponRepository
from persist.repositories.secondary_weapon_repository import SecondaryWeaponRepository
from persist.repositories.turret_weapon_repository import TurretWeaponRepository
from persist.repositories.ship_repository import ShipRepository
from persist.repositories.system_repository import SystemRepository
from persist.repositories.criminal_repository import CriminalRepository
from routers.data import DataCategory
import shared.bblogger as bblogger

flogger = bblogger.get_logger("bot-about-router")

router = APIRouter(prefix="/about", tags=["about"])

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
    compatible_skins: Optional[List[str]] = None
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

# Repository instances
module_repo = ModuleRepository()
primary_weapon_repo = PrimaryWeaponRepository()
secondary_weapon_repo = SecondaryWeaponRepository()
turret_weapon_repo = TurretWeaponRepository()
ship_repo = ShipRepository()
system_repo    = SystemRepository()
criminal_repo  = CriminalRepository()

# Category to repository mapping
CATEGORY_REPOS = {
    DataCategory.module: module_repo,
    DataCategory.primary: primary_weapon_repo,
    DataCategory.secondary: secondary_weapon_repo,
    DataCategory.turret: turret_weapon_repo,
    DataCategory.ship: ship_repo,
    DataCategory.system:   system_repo,
    DataCategory.criminal: criminal_repo,
}

# Category to response model mapping
CATEGORY_RESPONSE_MODELS = {
    DataCategory.module: ModuleResponse,
    DataCategory.primary: PrimaryWeaponResponse,
    DataCategory.secondary: SecondaryWeaponResponse,
    DataCategory.turret: TurretWeaponResponse,
    DataCategory.ship: ShipResponse,  
    DataCategory.system:   SystemResponse,
    DataCategory.criminal: CriminalResponse,
}

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with db_manager.get_session() as session:
        yield session

@router.get("/categories", response_model=List[str])
async def list_categories():
    """
    GET /about/categories
    Returns all valid object categories for menu population.
    """
    try:
        categories = [category.value for category in DataCategory]
        flogger.debug(f"Returning categories: {categories}")
        return categories
    except Exception as e:
        flogger.error(f"Error retrieving categories: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/categories/{category}/objects", response_model=List[Dict[str, Any]])
async def list_objects_for_category(category: DataCategory, db: AsyncSession = Depends(get_db)):
    """
    GET /about/categories/{category}/objects
    Returns all objects for a specified category for menu population.
    """
    try:
        if category not in CATEGORY_REPOS:
            raise HTTPException(status_code=404, detail=f"Category {category.value} not found")

        repo = CATEGORY_REPOS[category]
        objects = await repo.list_all(db)

        # Convert to simplified format for dropdown menus
        result = []
        for obj in objects:
            result.append({
                "id": obj.id,
                "name": obj.name,
                "aliases": obj.aliases if hasattr(obj, 'aliases') else [],
                "emoji": obj.emoji if hasattr(obj, 'emoji') else None
            })

        flogger.debug(f"Returning {len(result)} objects for category {category.value}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error retrieving objects for category {category.value}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/object/name/{object_name}", response_model=Dict[str, Any])
async def get_object_by_name(object_name: str, db: AsyncSession = Depends(get_db)):
    """
    GET /about/object/name/{object_name}
    Get detailed object information by name.
    """
    try:
        # Try each repository to find the object
        for category, repo in CATEGORY_REPOS.items():
            obj = await repo.get_by_name(db, object_name)
            if obj:
                # Convert to dict format
                result: Dict[str, Any] = {
                    "id": obj.id,
                    "name": obj.name,
                    "aliases": obj.aliases or [],
                    "built_in": getattr(obj, "built_in", None),
                    "emoji": getattr(obj, "emoji", None),
                    "icon": getattr(obj, "icon", None),
                    "value": getattr(obj, "value", None),
                    "wiki": getattr(obj, "wiki", None),
                    "type": getattr(obj, "type", None),
                    "tech_level": getattr(obj, "tech_level", None),
                    "extra_atts": getattr(obj, "extra_atts", None),
                    "category": category.value
                }

                if category == DataCategory.module:
                    result["max_equipped"] = obj.max_equipped
                elif category == DataCategory.primary:
                    result["dps"] = obj.dps
                elif category == DataCategory.ship:
                    result.update({
                        "armour": obj.armour,
                        "cargo": obj.cargo,
                        "handling": obj.handling,
                        "shop_spawn_rate": obj.shop_spawn_rate,
                        "max_modules": obj.max_modules,
                        "max_primaries": obj.max_primaries,
                        "max_secondaries": obj.max_secondaries,
                        "max_turrets": obj.max_turrets,
                        "manufacturer": obj.manufacturer,
                        "skinnable": obj.skinnable,
                        "compatible_skins": obj.compatible_skins or [],
                        "model": obj.model,
                        "norm_spec": obj.norm_spec,
                        "assets": obj.assets or [],
                        "save_due": obj.save_due,
                    })
                elif category == DataCategory.criminal:
                    # result["is_player"] = obj.is_player
                    result["faction"]   = obj.faction
                elif category == DataCategory.system:
                    result["coordinates"] = obj.coordinates
                    result["faction"]     = obj.faction

                flogger.debug(f"Found object '{object_name}' in category {category.value}")
                return result

        # Object not found in any category
        raise HTTPException(status_code=404, detail=f"Object with name '{object_name}' not found")
    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error retrieving object '{object_name}': {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/object/alias/{alias}", response_model=Dict[str, Any])
async def get_object_by_alias(alias: str, db: AsyncSession = Depends(get_db)):
    """
    GET /about/object/alias/{alias}
    Get detailed object information by any of its aliases.
    """
    try:
        for category, repo in CATEGORY_REPOS.items():
            obj = await repo.get_by_alias(db, alias)
            if obj:
                result: Dict[str, Any] = {
                    "id": obj.id,
                    "name": obj.name,
                    "aliases": obj.aliases or [],
                    "built_in": getattr(obj, "built_in", None),
                    "emoji": getattr(obj, "emoji", None),
                    "icon": getattr(obj, "icon", None),
                    "value": getattr(obj, "value", None),
                    "wiki": getattr(obj, "wiki", None),
                    "type": getattr(obj, "type", None),
                    "tech_level": getattr(obj, "tech_level", None),
                    "extra_atts": getattr(obj, "extra_atts", None),
                    "category": category.value
                }

                if category == DataCategory.module:
                    result["max_equipped"] = obj.max_equipped
                elif category == DataCategory.primary:
                    result["dps"] = obj.dps
                elif category == DataCategory.ship:
                    result.update({
                        "armour": obj.armour,
                        "cargo": obj.cargo,
                        "handling": obj.handling,
                        "shop_spawn_rate": obj.shop_spawn_rate,
                        "max_modules": obj.max_modules,
                        "max_primaries": obj.max_primaries,
                        "max_secondaries": obj.max_secondaries,
                        "max_turrets": obj.max_turrets,
                        "manufacturer": obj.manufacturer,
                        "skinnable": obj.skinnable,
                        "compatible_skins": obj.compatible_skins or [],
                        "model": obj.model,
                        "norm_spec": obj.norm_spec,
                        "assets": obj.assets or [],
                        "save_due": obj.save_due,
                    })
                elif category == DataCategory.criminal:
                    # result["is_player"] = obj.is_player
                    result["faction"]   = obj.faction
                elif category == DataCategory.system:
                    result["coordinates"] = obj.coordinates
                    result["faction"]     = obj.faction

                flogger.debug(f"Found object by alias '{alias}' in {category.value}")
                return result

        raise HTTPException(404, detail=f"Object with alias '{alias}' not found")
    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error retrieving object by alias '{alias}': {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/object/{category}/{object_id}", response_model=Dict[str, Any])
async def get_object_by_id(category: DataCategory, object_id: int, db: AsyncSession = Depends(get_db)):
    """
    GET /about/object/{category}/{object_id}
    Get detailed object information by ID.
    """
    repo = CATEGORY_REPOS.get(category)
    if not repo:
        raise HTTPException(404, detail=f"Category {category.value} not found")

    obj = await repo.get_by_id(db, object_id)
    if not obj:
        raise HTTPException(404, detail=f"{category.value.title()} with ID {object_id} not found")
    try:
        result: Dict[str, Any] = {
            "id": obj.id,
            "name": obj.name,
            "aliases": obj.aliases or [],
            "built_in": getattr(obj, "built_in", None),
            "emoji": getattr(obj, "emoji", None),
            "icon": getattr(obj, "icon", None),
            "value": getattr(obj, "value", None),
            "wiki": getattr(obj, "wiki", None),
            "type": getattr(obj, "type", None),
            "tech_level": getattr(obj, "tech_level", None),
            "extra_atts": getattr(obj, "extra_atts", None),
            "category": category.value
        }

        if category == DataCategory.module:
            result["max_equipped"] = obj.max_equipped
        elif category == DataCategory.primary:
            result["dps"] = obj.dps
        elif category == DataCategory.ship:
            result.update({
                "armour": obj.armour,
                "cargo": obj.cargo,
                "handling": obj.handling,
                "shop_spawn_rate": obj.shop_spawn_rate,
                "max_modules": obj.max_modules,
                "max_primaries": obj.max_primaries,
                "max_secondaries": obj.max_secondaries,
                "max_turrets": obj.max_turrets,
                "manufacturer": obj.manufacturer,
                "skinnable": obj.skinnable,
                "compatible_skins": obj.compatible_skins or [],
                "model": obj.model,
                "norm_spec": obj.norm_spec,
                "assets": obj.assets or [],
                "save_due": obj.save_due,
            })
        elif category == DataCategory.criminal:
            # result["is_player"] = obj.is_player
            result["faction"]   = obj.faction
        elif category == DataCategory.system:
            result["coordinates"] = obj.coordinates
            result["faction"]     = obj.faction
            result["neighbours"]  = getattr(obj, "neighbours", None)
            result["security"]    = getattr(obj, "security", None)

        flogger.debug(f"Found {category.value} {object_id}")
        return result

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error retrieving object {object_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")