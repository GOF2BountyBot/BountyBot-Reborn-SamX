from enum import Enum
from typing import List, Optional, Dict, Any, Generator
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel

from persist.database.manager import db_manager
from persist.repositories.module_repository import ModuleRepository
from persist.repositories.primary_weapon_repository import PrimaryWeaponRepository
from persist.repositories.secondary_weapon_repository import SecondaryWeaponRepository
from persist.repositories.turret_weapon_repository import TurretWeaponRepository
from routers.data import DataCategory
import shared.logging as logging

logger = logging.get_logger("bot-about-router")

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

# Repository instances
module_repo = ModuleRepository()
primary_weapon_repo = PrimaryWeaponRepository()
secondary_weapon_repo = SecondaryWeaponRepository()
turret_weapon_repo = TurretWeaponRepository()

# Category to repository mapping
CATEGORY_REPOS = {
    DataCategory.module: module_repo,
    DataCategory.primary: primary_weapon_repo,
    DataCategory.secondary: secondary_weapon_repo,
    DataCategory.turret: turret_weapon_repo,
}

# Category to response model mapping
CATEGORY_RESPONSE_MODELS = {
    DataCategory.module: ModuleResponse,
    DataCategory.primary: PrimaryWeaponResponse,
    DataCategory.secondary: SecondaryWeaponResponse,
    DataCategory.turret: TurretWeaponResponse,
}

def get_db() -> Generator[Session, None, None]:
    # open the context‐manager, grab the Session, yield it to FastAPI,
    # then __exit__ automatically closes it.
    with db_manager.get_session() as session:
        yield session

@router.get("/categories", response_model=List[str])
def list_categories():
    """
    GET /about/categories
    Returns all valid object categories for menu population.
    """
    try:
        categories = [category.value for category in DataCategory]
        logger.debug(f"Returning categories: {categories}")
        return categories
    except Exception as e:
        logger.error(f"Error retrieving categories: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/categories/{category}/objects", response_model=List[Dict[str, Any]])
def list_objects_for_category(category: DataCategory, db: Session = Depends(get_db)):
    """
    GET /about/categories/{category}/objects
    Returns all objects for a specified category for menu population.
    """
    try:
        if category not in CATEGORY_REPOS:
            raise HTTPException(status_code=404, detail=f"Category {category.value} not found")

        repo = CATEGORY_REPOS[category]
        objects = repo.list_all(db)

        # Convert to simplified format for dropdown menus
        result = []
        for obj in objects:
            result.append({
                "id": obj.id,
                "name": obj.name,
                "aliases": obj.aliases if hasattr(obj, 'aliases') else [],
                "emoji": obj.emoji if hasattr(obj, 'emoji') else None
            })

        logger.debug(f"Returning {len(result)} objects for category {category.value}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving objects for category {category.value}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/object/{object_id}", response_model=Dict[str, Any])
def get_object_by_id(object_id: int, db: Session = Depends(get_db)):
    """
    GET /about/object/{object_id}
    Get detailed object information by ID.
    """
    try:
        # Try each repository to find the object
        for category, repo in CATEGORY_REPOS.items():
            obj = repo.get_by_id(db, object_id)
            if obj:
                # Convert to dict format
                result = {
                    "id": obj.id,
                    "name": obj.name,
                    "aliases": obj.aliases if hasattr(obj, 'aliases') else [],
                    "built_in": obj.built_in if hasattr(obj, 'built_in') else False,
                    "emoji": obj.emoji if hasattr(obj, 'emoji') else None,
                    "icon": obj.icon if hasattr(obj, 'icon') else None,
                    "value": obj.value if hasattr(obj, 'value') else None,
                    "wiki": obj.wiki if hasattr(obj, 'wiki') else None,
                    "type": obj.type if hasattr(obj, 'type') else None,
                    "tech_level": obj.tech_level if hasattr(obj, 'tech_level') else None,
                    "extra_atts": obj.extra_atts if hasattr(obj, 'extra_atts') else None,
                    "category": category.value
                }

                # Add specific fields based on category
                if category == DataCategory.module and hasattr(obj, 'max_equipped'):
                    result["max_equipped"] = obj.max_equipped
                elif category == DataCategory.primary and hasattr(obj, 'dps'):
                    result["dps"] = obj.dps

                logger.debug(f"Found object {object_id} in category {category.value}")
                return result

        # Object not found in any category
        raise HTTPException(status_code=404, detail=f"Object with ID {object_id} not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving object {object_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/object/name/{object_name}", response_model=Dict[str, Any])
def get_object_by_name(object_name: str, db: Session = Depends(get_db)):
    """
    GET /about/object/name/{object_name}
    Get detailed object information by name.
    """
    try:
        # Try each repository to find the object
        for category, repo in CATEGORY_REPOS.items():
            obj = repo.get_by_name(db, object_name)
            if obj:
                # Convert to dict format
                result = {
                    "id": obj.id,
                    "name": obj.name,
                    "aliases": obj.aliases if hasattr(obj, 'aliases') else [],
                    "built_in": obj.built_in if hasattr(obj, 'built_in') else False,
                    "emoji": obj.emoji if hasattr(obj, 'emoji') else None,
                    "icon": obj.icon if hasattr(obj, 'icon') else None,
                    "value": obj.value if hasattr(obj, 'value') else None,
                    "wiki": obj.wiki if hasattr(obj, 'wiki') else None,
                    "type": obj.type if hasattr(obj, 'type') else None,
                    "tech_level": obj.tech_level if hasattr(obj, 'tech_level') else None,
                    "extra_atts": obj.extra_atts if hasattr(obj, 'extra_atts') else None,
                    "category": category.value
                }

                # Add specific fields based on category
                if category == DataCategory.module and hasattr(obj, 'max_equipped'):
                    result["max_equipped"] = obj.max_equipped
                elif category == DataCategory.primary and hasattr(obj, 'dps'):
                    result["dps"] = obj.dps

                logger.debug(f"Found object '{object_name}' in category {category.value}")
                return result

        # Object not found in any category
        raise HTTPException(status_code=404, detail=f"Object with name '{object_name}' not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving object '{object_name}': {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/object/alias/{alias}", response_model=Dict[str, Any])
def get_object_by_alias(alias: str, db: Session = Depends(get_db)):
    """
    GET /about/object/alias/{alias}
    Get detailed object information by any of its aliases.
    """
    try:
        for category, repo in CATEGORY_REPOS.items():
            obj = repo.get_by_alias(db, alias)
            if obj:
                result = {
                    "id": obj.id,
                    "name": obj.name,
                    "aliases": obj.aliases if hasattr(obj, "aliases") else [],
                    "built_in": getattr(obj, "built_in", False),
                    "emoji": getattr(obj, "emoji", None),
                    "icon": getattr(obj, "icon", None),
                    "value": getattr(obj, "value", None),
                    "wiki": getattr(obj, "wiki", None),
                    "type": getattr(obj, "type", None),
                    "tech_level": getattr(obj, "tech_level", None),
                    "extra_atts": getattr(obj, "extra_atts", None),
                    "category": category.value
                }
                if category == DataCategory.module and hasattr(obj, "max_equipped"):
                    result["max_equipped"] = obj.max_equipped
                elif category == DataCategory.primary and hasattr(obj, "dps"):
                    result["dps"] = obj.dps

                logger.debug(f"Found object by alias '{alias}' in {category.value}")
                return result

        raise HTTPException(404, detail=f"Object with alias '{alias}' not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving object by alias '{alias}': {e}")
        raise HTTPException(500, detail="Internal server error")
