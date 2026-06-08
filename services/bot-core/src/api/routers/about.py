from collections.abc import AsyncGenerator
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from persist.database.manager import db_manager
from persist.repositories.commodity_repository import CommodityRepository
from persist.repositories.criminal_repository import CriminalRepository
from persist.repositories.module_repository import ModuleRepository
from persist.repositories.primary_weapon_repository import PrimaryWeaponRepository
from persist.repositories.secondary_weapon_repository import SecondaryWeaponRepository
from persist.repositories.ship_repository import ShipRepository
from persist.repositories.system_repository import SystemRepository
from persist.repositories.turret_weapon_repository import TurretWeaponRepository
from services.game_constants import GameConstants
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

from api.routers.data import DataCategory
from api.schemas.about_schema import (
    CommodityResponse,
    CriminalResponse,
    ModuleResponse,
    PrimaryWeaponResponse,
    SecondaryWeaponResponse,
    ShipResponse,
    SystemResponse,
    TurretWeaponResponse,
)

flogger = bblogger.get_logger("bot-about-router")

router = APIRouter(prefix="/about", tags=["about"])


def _enrich_combat_fields(result: dict[str, Any], category: str) -> None:
    """Populate §14 / T11 combat fields from extra_atts into the response dict.

    DB storage pattern (all weapon/module categories):
        outer = obj.extra_atts  (e.g. {"builtIn": ..., "extra_atts": {<inner>}})
        inner = outer.get("extra_atts", outer)   <- combat-relevant fields live here

    Module PrimaryWeaponMod pattern:
        outer["dpsMultiplier"]       <- camelCase, top-level in seed → top-level in outer
        inner["damage_pct"]          <- snake_case, in nested extra_atts
        inner["fire_rate_pct"]       <- snake_case, in nested extra_atts

    Modifies *result* in-place; safe to call on any category (no-op for non-weapon/module).
    """
    extra_outer: dict[str, Any] = result.get("extra_atts") or {}
    # Unpack inner extra_atts (DB nesting pattern — see loadout_builder.py line ~200)
    extra_inner: dict[str, Any] = extra_outer.get("extra_atts", extra_outer) if isinstance(extra_outer, dict) else {}
    subtype: str = extra_inner.get("subtype", "") if isinstance(extra_inner, dict) else ""

    if category in ("primary_weapon", "secondary_weapon", "turret_weapon"):
        # EMP damage: any weapon with emp_damage > 0 in inner extra_atts
        raw_emp = extra_inner.get("emp_damage") if isinstance(extra_inner, dict) else None
        emp = int(raw_emp) if raw_emp is not None else None
        result["emp_damage"] = emp if emp else None

    if category == "secondary_weapon":
        # Cluster-missile burst_count
        raw_burst = extra_inner.get("burst_count") if isinstance(extra_inner, dict) else None
        result["burst_count"] = int(raw_burst) if raw_burst is not None else None

        # Nuke fields
        if subtype == "nuke":
            damage_val = result.get("damage")
            result["nuke_direct_damage"] = int(damage_val) if damage_val is not None else None
            raw_mag = extra_inner.get("magnitude_m") if isinstance(extra_inner, dict) else None
            if raw_mag is not None:
                result["nuke_effective_magnitude_m"] = round(float(raw_mag) * GameConstants.NUKE_MAGNITUDE_SCALE)
            else:
                result["nuke_effective_magnitude_m"] = None
            result["nuke_self_damage_factor"] = GameConstants.NUKE_FRIENDLY_FACTOR
        else:
            result["nuke_direct_damage"] = None
            result["nuke_effective_magnitude_m"] = None
            result["nuke_self_damage_factor"] = None

    if category == "module":
        item_type: str = result.get("type") or ""
        if item_type == "PrimaryWeaponModModule":
            # dpsMultiplier is camelCase at OUTER level (top-level in seed → outer extra_atts)
            raw_dps_mult = extra_outer.get("dpsMultiplier") if isinstance(extra_outer, dict) else None
            result["dps_multiplier"] = float(raw_dps_mult) if raw_dps_mult is not None else None
            # damage_pct and fire_rate_pct are in INNER extra_atts (snake_case)
            raw_dmg_pct = extra_inner.get("damage_pct") if isinstance(extra_inner, dict) else None
            result["damage_pct"] = int(raw_dmg_pct) if raw_dmg_pct is not None else None
            raw_fr_pct = extra_inner.get("fire_rate_pct") if isinstance(extra_inner, dict) else None
            result["fire_rate_pct"] = int(raw_fr_pct) if raw_fr_pct is not None else None
        else:
            result["dps_multiplier"] = None
            result["damage_pct"] = None
            result["fire_rate_pct"] = None


# Repository instances
module_repo = ModuleRepository()
primary_weapon_repo = PrimaryWeaponRepository()
secondary_weapon_repo = SecondaryWeaponRepository()
turret_weapon_repo = TurretWeaponRepository()
ship_repo = ShipRepository()
system_repo = SystemRepository()
criminal_repo = CriminalRepository()
commodity_repo = CommodityRepository()

# Category to repository mapping
CATEGORY_REPOS = {
    DataCategory.module: module_repo,
    DataCategory.primary: primary_weapon_repo,
    DataCategory.secondary: secondary_weapon_repo,
    DataCategory.turret: turret_weapon_repo,
    DataCategory.ship: ship_repo,
    DataCategory.system: system_repo,
    DataCategory.criminal: criminal_repo,
    DataCategory.commodity: commodity_repo,
}

# Category to response model mapping
CATEGORY_RESPONSE_MODELS = {
    DataCategory.module: ModuleResponse,
    DataCategory.primary: PrimaryWeaponResponse,
    DataCategory.secondary: SecondaryWeaponResponse,
    DataCategory.turret: TurretWeaponResponse,
    DataCategory.ship: ShipResponse,
    DataCategory.system: SystemResponse,
    DataCategory.criminal: CriminalResponse,
}


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with db_manager.get_session() as session:
        yield session


@router.get("/categories", response_model=list[str])
async def list_categories():
    """
    GET /about/categories
    Returns all valid object categories for menu population.
    """
    flogger.info("Request received: GET /categories")
    try:
        categories = [category.value for category in DataCategory]
        flogger.debug(f"Categories retrieved: count={len(categories)}, values={categories}")
        return categories
    except Exception as e:
        flogger.exception(f"Error retrieving categories: error={e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/categories/{category}/objects", response_model=list[dict[str, Any]])
async def list_objects_for_category(category: DataCategory, db: AsyncSession = Depends(get_db)):
    """
    GET /about/categories/{category}/objects
    Returns all objects for a specified category for menu population.
    """
    flogger.info(f"Request received: GET /categories/{{category}}/objects, category={category.value}")
    try:
        if category not in CATEGORY_REPOS:
            raise HTTPException(status_code=404, detail=f"Category {category.value} not found")

        repo = CATEGORY_REPOS[category]
        objects = await repo.list_all(db)

        # Convert to simplified format for dropdown menus
        result = []
        for obj in objects:
            result.append(
                {
                    "id": obj.id,
                    "name": obj.name,
                    "aliases": obj.aliases if hasattr(obj, "aliases") else [],
                    "emoji": obj.emoji if hasattr(obj, "emoji") else None,
                    "tech_level": getattr(obj, "tech_level", None),
                    "manufacturer": getattr(obj, "manufacturer", None),
                }
            )

        flogger.debug(f"Objects retrieved: count={len(result)}, category={category.value}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        flogger.exception(f"Error retrieving objects: category={category.value}, error={e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


def _build_object_result(obj: Any, category: DataCategory) -> dict[str, Any]:
    """Build the result dict for a matched game object.

    Shared by ``get_object_by_name`` and ``get_object_by_alias`` so both endpoints
    produce identical output from the same first-match logic.  Only category-specific
    fields that differ from the common base are appended; combat enrichment is applied
    at the end.

    Note: this helper does NOT include ``neighbours``/``security`` for systems — those
    are exclusive to ``get_object_by_id`` which has a richer system view.
    """
    result: dict[str, Any] = {
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
        "category": category.value,
    }

    if category == DataCategory.module:
        result["max_equipped"] = obj.max_equipped
    elif category == DataCategory.primary:
        result["dps"] = obj.dps
    elif category == DataCategory.secondary:
        result["damage"] = obj.damage
        result["loading_speed"] = obj.loading_speed
    elif category == DataCategory.turret:
        result["dps"] = obj.dps
    elif category == DataCategory.ship:
        result.update(
            {
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
                "builtin_modules": obj.builtin_modules or [],
            }
        )
    elif category == DataCategory.criminal:
        # result["is_player"] = obj.is_player
        result["faction"] = obj.faction
    elif category == DataCategory.system:
        result["coordinates"] = obj.coordinates
        result["faction"] = obj.faction
    elif category == DataCategory.commodity:
        result.update(CommodityResponse.model_validate(obj).model_dump())

    _enrich_combat_fields(result, category.value)
    return result


@router.get("/object/name/{object_name}", response_model=dict[str, Any])
async def get_object_by_name(object_name: str, db: AsyncSession = Depends(get_db)):
    """
    GET /about/object/name/{object_name}
    Get detailed object information by name.
    """
    flogger.info(f"Request received: GET /object/name/{{object_name}}, object_name={object_name}")
    try:
        # Scan repos in declaration order; return on first match (short-circuit).
        for category, repo in CATEGORY_REPOS.items():
            obj = await repo.get_by_name(db, object_name)
            if obj:
                result = _build_object_result(obj, category)
                flogger.debug(f"Object found: name={object_name}, category={category.value}, id={obj.id}")
                return result

        # Object not found in any category
        flogger.debug(f"Object not found: name={object_name}")
        raise HTTPException(status_code=404, detail=f"Object with name '{object_name}' not found")
    except HTTPException:
        raise
    except Exception as e:
        flogger.exception(f"Error retrieving object by name: object_name={object_name}, error={e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/object/alias/{alias}", response_model=dict[str, Any])
async def get_object_by_alias(alias: str, db: AsyncSession = Depends(get_db)):
    """
    GET /about/object/alias/{alias}
    Get detailed object information by any of its aliases.
    """
    flogger.info(f"Request received: GET /object/alias/{{alias}}, alias={alias}")
    try:
        # Scan repos in declaration order; return on first match (short-circuit).
        for category, repo in CATEGORY_REPOS.items():
            obj = await repo.get_by_alias(db, alias)
            if obj:
                result = _build_object_result(obj, category)
                flogger.debug(f"Object found by alias: alias={alias}, category={category.value}, id={obj.id}")
                return result

        flogger.debug(f"Object not found by alias: alias={alias}")
        raise HTTPException(404, detail=f"Object with alias '{alias}' not found")
    except HTTPException:
        raise
    except Exception as e:
        flogger.exception(f"Error retrieving object by alias: alias={alias}, error={e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/object/{category}/{object_id}", response_model=dict[str, Any])
async def get_object_by_id(category: DataCategory, object_id: int, db: AsyncSession = Depends(get_db)):
    """
    GET /about/object/{category}/{object_id}
    Get detailed object information by ID.
    """
    flogger.info(
        f"Request received: GET /object/{{category}}/{{object_id}}, category={category.value}, object_id={object_id}"
    )
    repo = CATEGORY_REPOS.get(category)
    if not repo:
        flogger.debug(f"Category not found: category={category.value}")
        raise HTTPException(404, detail=f"Category {category.value} not found")

    obj = await repo.get_by_id(db, object_id)
    if not obj:
        flogger.debug(f"Object not found: category={category.value}, object_id={object_id}")
        raise HTTPException(404, detail=f"{category.value.title()} with ID {object_id} not found")
    try:
        result: dict[str, Any] = {
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
            "category": category.value,
        }

        if category == DataCategory.module:
            result["max_equipped"] = obj.max_equipped
        elif category == DataCategory.primary:
            result["dps"] = obj.dps
        elif category == DataCategory.secondary:
            result["damage"] = obj.damage
            result["loading_speed"] = obj.loading_speed
        elif category == DataCategory.turret:
            result["dps"] = obj.dps
        elif category == DataCategory.ship:
            result.update(
                {
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
                    "builtin_modules": obj.builtin_modules or [],
                }
            )
        elif category == DataCategory.criminal:
            # result["is_player"] = obj.is_player
            result["faction"] = obj.faction
        elif category == DataCategory.system:
            result["coordinates"] = obj.coordinates
            result["faction"] = obj.faction
            result["neighbours"] = getattr(obj, "neighbours", None)
            result["security"] = getattr(obj, "security", None)
        elif category == DataCategory.commodity:
            result.update(CommodityResponse.model_validate(obj).model_dump())

        _enrich_combat_fields(result, category.value)
        flogger.debug(f"Object retrieved by ID: category={category.value}, object_id={object_id}")
        return result

    except HTTPException:
        raise
    except Exception as e:
        flogger.exception(f"Error retrieving object: category={category.value}, object_id={object_id}, error={e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get(
    "/ships/{ship_name}/render-info",
    summary="Get ship rendering metadata",
    description=(
        "Returns rendering metadata for a ship (model path, mask paths, skinBase, "
        "texture regions). Used by blender-service for skin rendering."
    ),
    status_code=status.HTTP_200_OK,
)
async def get_ship_render_info(
    ship_name: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """GET /about/ships/{ship_name}/render-info

    Returns structured rendering metadata for skinnable ships.
    Raises 404 for unknown ships or non-skinnable ships.
    """
    flogger.info(f"Request received: GET /ships/{{ship_name}}/render-info, ship_name={ship_name}")
    try:
        ship = await ship_repo.get_by_name(db, ship_name)
        if not ship:
            flogger.debug(f"Ship not found: ship_name={ship_name}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Ship '{ship_name}' not found",
            )

        if not ship.skinnable:
            flogger.debug(f"Ship is not skinnable: ship_name={ship_name}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Ship is not skinnable",
            )

        def _resolve_asset_path(path: str | None) -> str | None:
            """Try the original path; if it doesn't exist, try with .png extension."""
            if path is None:
                return None
            p = Path(path)
            if p.exists():
                return path
            # Try .png fallback (upscaled textures replaced .bmp/.jpg with .png)
            png_path = p.with_suffix(".png")
            if png_path.exists():
                return str(png_path)
            return path  # return original even if not found (let caller handle)

        assets: list[str] = ship.assets or []

        # Extract file paths from the assets list
        mtl_path: str | None = next((a for a in assets if a.endswith(".mtl")), None)
        skin_base_path: str | None = next((a for a in assets if "skinBase" in a), None)
        diffuse_path: str | None = _resolve_asset_path(next((a for a in assets if "_diffuse" in a), None))

        # Collect mask files (mask1.jpg, mask2.jpg …) ordered numerically
        import re

        def _mask_sort_key(path: str) -> int:
            m = re.search(r"mask(\d+)", path)
            return int(m.group(1)) if m else 0

        mask_paths: list[str] = [
            _resolve_asset_path(p) or p
            for p in sorted(
                [a for a in assets if re.search(r"mask\d+", a)],
                key=_mask_sort_key,
            )
        ]

        # Derive the bbship directory from the model path (parent directory)
        bbship_dir: str | None = None
        if ship.model:
            bbship_dir = str(PurePosixPath(ship.model).parent)

        flogger.debug(
            f"Render info retrieved: ship_name={ship_name}, "
            f"texture_regions={ship.texture_regions}, masks={len(mask_paths)}"
        )
        return {
            "name": ship.name,
            "skinnable": ship.skinnable,
            "texture_regions": ship.texture_regions,
            "model_path": ship.model,
            "mtl_path": mtl_path,
            "skin_base_path": skin_base_path,
            "norm_spec_path": _resolve_asset_path(ship.norm_spec),
            "diffuse_path": diffuse_path,
            "mask_paths": mask_paths,
            "compatible_skins": ship.compatible_skins or {},
            "bbship_dir": bbship_dir,
        }

    except HTTPException:
        raise
    except Exception as e:
        flogger.exception(f"Error retrieving render info: ship_name={ship_name}, error={e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e
