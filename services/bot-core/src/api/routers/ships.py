"""
Ships API router for the BountyBot inventory system.

Handles REST API endpoints for ship management including ownership,
loadout management, and active ship selection.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from persist.database.manager import get_db_session
from persist.repositories.player_repository import PlayerRepository
from persist.repositories.player_ship_repository import PlayerShipRepository
from persist.repositories.ship_repository import ShipRepository
from services.equipment_service import EquipmentService
from shared import bblogger

from api.schemas.ships_schema import (
    CreateShipRequest,
    EquipCheckRequest,
    EquipCheckResponse,
    EquipItemRequest,
    ShipLoadoutSummaryResponse,
    ShipResponse,
    TransferShipRequest,
    TransferShipResponse,
    UnequipItemRequest,
    UpdateLoadoutRequest,
    UpdateNicknameRequest,
)

flogger = bblogger.get_logger("ships-api-router")

router = APIRouter(
    prefix="/ships",
    tags=["ships"],
    responses={404: {"description": "Ship or player not found"}, 500: {"description": "Internal server error"}},
)


# Item type → equipment category mapping
_ITEM_TYPE_TO_EQUIPMENT_CATEGORY: dict[str, str] = {
    "PrimaryWeapon": "weapons",
    "SecondaryWeapon": "weapons",
    "TurretWeapon": "turrets",
}

# Module class name prefix to identify modules
_MODULE_TYPE_SUFFIXES = {"Module"}


def _item_type_to_equipment_category(item_type: str) -> str | None:
    """Map an Item.type value to an equipment category string.

    Returns one of ``"weapons"``, ``"modules"``, ``"turrets"``, or ``None`` if
    the type is not equippable.
    """
    if item_type in _ITEM_TYPE_TO_EQUIPMENT_CATEGORY:
        return _ITEM_TYPE_TO_EQUIPMENT_CATEGORY[item_type]
    # Any type ending in "Module" is a module
    if item_type.endswith("Module"):
        return "modules"
    return None


# Dependency injection
async def get_ship_repository():
    return ShipRepository()


async def get_player_repository():
    return PlayerRepository()


async def get_player_ship_repository():
    return PlayerShipRepository()


async def get_equipment_service():
    return EquipmentService()


async def get_loadout_consistency_service():
    """Dependency factory for LoadoutConsistencyService.

    Lives at module scope so router tests can override it via
    ``app.dependency_overrides`` in the same way they override the repo and
    equipment-service factories (Package G B.19).
    """
    from services.loadout_consistency_service import LoadoutConsistencyService

    return LoadoutConsistencyService()


@router.get("/player/{player_id}", response_model=list[ShipResponse])
async def get_player_ships(
    player_id: int,
    player_ship_repo: PlayerShipRepository = Depends(get_player_ship_repository),
):
    """Get all ships owned by a player."""
    flogger.debug(f"Getting ships for player {player_id}")

    try:
        async with get_db_session() as db:
            ships = await player_ship_repo.get_player_ships(db, player_id)

            return [
                ShipResponse(
                    id=ship.id,
                    player_id=ship.player_id,
                    ship_name=ship.ship_name,
                    nickname=ship.nickname,
                    is_active=ship.is_active,
                    weapons=ship.weapons,
                    modules=ship.modules,
                    turrets=ship.turrets,
                    secondary_weapons=ship.secondary_weapons,
                    created_at=ship.created_at.isoformat(),
                )
                for ship in ships
            ]

    except Exception as e:
        flogger.error(f"Error getting ships for player {player_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get player ships"
        ) from e


@router.get("/{ship_id}", response_model=ShipResponse)
async def get_ship(
    ship_id: int,
    player_ship_repo: PlayerShipRepository = Depends(get_player_ship_repository),
):
    """Get a specific ship by ID."""
    flogger.debug(f"Getting ship {ship_id}")

    try:
        async with get_db_session() as db:
            ship = await player_ship_repo.get_by_id(db, ship_id)
            if not ship:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ship {ship_id} not found")

            return ShipResponse(
                id=ship.id,
                player_id=ship.player_id,
                ship_name=ship.ship_name,
                nickname=ship.nickname,
                is_active=ship.is_active,
                weapons=ship.weapons,
                modules=ship.modules,
                turrets=ship.turrets,
                secondary_weapons=ship.secondary_weapons,
                created_at=ship.created_at.isoformat(),
            )

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error getting ship {ship_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get ship") from e


@router.post("/", response_model=ShipResponse, status_code=status.HTTP_201_CREATED)
async def create_ship(
    request: CreateShipRequest,
    player_ship_repo: PlayerShipRepository = Depends(get_player_ship_repository),
    player_repo: PlayerRepository = Depends(get_player_repository),
):
    """Create a new ship for a player."""
    flogger.info(f"Creating ship {request.ship_name} for player {request.player_id}")

    try:
        async with get_db_session() as db:
            # Verify player exists
            player = await player_repo.get_by_id(db, request.player_id)
            if not player:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=f"Player {request.player_id} not found"
                )

            # Create ship
            ship_data = request.model_dump()
            ship = await player_ship_repo.create_or_update(db, ship_data)

            return ShipResponse(
                id=ship.id,
                player_id=ship.player_id,
                ship_name=ship.ship_name,
                nickname=ship.nickname,
                is_active=ship.is_active,
                weapons=ship.weapons,
                modules=ship.modules,
                turrets=ship.turrets,
                secondary_weapons=ship.secondary_weapons,
                created_at=ship.created_at.isoformat(),
            )

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error creating ship: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create ship") from e


@router.get("/player/{player_id}/active", response_model=ShipResponse | None)
async def get_active_ship(
    player_id: int,
    player_ship_repo: PlayerShipRepository = Depends(get_player_ship_repository),
):
    """Get the active ship for a player."""
    flogger.debug(f"Getting active ship for player {player_id}")

    try:
        async with get_db_session() as db:
            ship = await player_ship_repo.get_active_ship(db, player_id)

            if not ship:
                return None

            return ShipResponse(
                id=ship.id,
                player_id=ship.player_id,
                ship_name=ship.ship_name,
                nickname=ship.nickname,
                is_active=ship.is_active,
                weapons=ship.weapons,
                modules=ship.modules,
                turrets=ship.turrets,
                secondary_weapons=ship.secondary_weapons,
                created_at=ship.created_at.isoformat(),
            )

    except Exception as e:
        flogger.error(f"Error getting active ship for player {player_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get active ship"
        ) from e


@router.put("/{ship_id}/set-active", response_model=ShipResponse)
async def set_active_ship(
    ship_id: int,
    player_id: int,
    player_repo: PlayerRepository = Depends(get_player_repository),
    consistency=Depends(get_loadout_consistency_service),
):
    """Set a ship as the active ship for a player.

    B.94/B.95: delegates entirely to the canonical ``activate_ship`` choke-point on
    ``LoadoutConsistencyService``, which:
    1. Reconciles the target ship's loadout against its static slot caps (I4).
    2. Transfers the loadout from the currently-active ship to the target with
       merge-with-overflow semantics (B.95 — gear follows the active ship).
    3. Flips the ``is_active`` flag.
    4. Updates ``Player.active_ship_id`` (fixes the B.94 stale-reference bug).

    The response includes ``evacuated_items`` for cogs that wish to render a
    "X items moved to cargo" notice.
    """
    flogger.info(f"Setting ship {ship_id} as active for player {player_id}")

    try:
        async with get_db_session() as db, db.begin():
            result = await consistency.activate_ship(
                db,
                player_id=player_id,
                target_ship_id=ship_id,
                player_repo=player_repo,
            )
            ship = result["ship"]

            return ShipResponse(
                id=ship.id,
                player_id=ship.player_id,
                ship_name=ship.ship_name,
                nickname=ship.nickname,
                is_active=ship.is_active,
                weapons=ship.weapons,
                modules=ship.modules,
                turrets=ship.turrets,
                secondary_weapons=ship.secondary_weapons,
                created_at=ship.created_at.isoformat(),
                evacuated_items=result["evacuated_items"],
                any_evacuated=result["any_evacuated"],
            )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error setting active ship: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to set active ship"
        ) from e


@router.put("/{ship_id}/loadout", response_model=ShipResponse)
async def update_ship_loadout(
    ship_id: int,
    request: UpdateLoadoutRequest,
    player_ship_repo: PlayerShipRepository = Depends(get_player_ship_repository),
):
    """Update a ship's equipment loadout."""
    flogger.info(f"Updating loadout for ship {ship_id}")

    try:
        async with get_db_session() as db:
            # Build loadout update dict
            loadout_updates = {}
            if request.weapons is not None:
                loadout_updates["weapons"] = request.weapons
            if request.modules is not None:
                loadout_updates["modules"] = request.modules
            if request.turrets is not None:
                loadout_updates["turrets"] = request.turrets

            ship = await player_ship_repo.update_loadout(db, ship_id, loadout_updates)

            return ShipResponse(
                id=ship.id,
                player_id=ship.player_id,
                ship_name=ship.ship_name,
                nickname=ship.nickname,
                is_active=ship.is_active,
                weapons=ship.weapons,
                modules=ship.modules,
                turrets=ship.turrets,
                secondary_weapons=ship.secondary_weapons,
                created_at=ship.created_at.isoformat(),
            )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error updating ship loadout: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update ship loadout"
        ) from e


@router.put("/{ship_id}/nickname", response_model=ShipResponse)
async def update_ship_nickname(
    ship_id: int,
    request: UpdateNicknameRequest,
    player_ship_repo: PlayerShipRepository = Depends(get_player_ship_repository),
):
    """Update a ship's nickname."""
    flogger.info(f"Updating nickname for ship {ship_id}: {request.nickname}")

    try:
        async with get_db_session() as db:
            ship = await player_ship_repo.update_nickname(db, ship_id, request.nickname)

            return ShipResponse(
                id=ship.id,
                player_id=ship.player_id,
                ship_name=ship.ship_name,
                nickname=ship.nickname,
                is_active=ship.is_active,
                weapons=ship.weapons,
                modules=ship.modules,
                turrets=ship.turrets,
                secondary_weapons=ship.secondary_weapons,
                created_at=ship.created_at.isoformat(),
            )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error updating ship nickname: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update ship nickname"
        ) from e


@router.post("/{ship_id}/equip-check", response_model=EquipCheckResponse)
async def equip_check(
    ship_id: int,
    request: EquipCheckRequest,
    equipment_service: EquipmentService = Depends(get_equipment_service),
):
    """Pre-flight check before equipping: auto-detect item type and validate slot/unique constraints.

    Returns one of three statuses:
    - ``"ok"`` — item can be equipped immediately
    - ``"slot_full"`` — all slots for this equipment type are occupied (includes equipped items for swap UI)
    - ``"unique_conflict"`` — a module with the same unique class is already equipped
    """
    flogger.info(f"equip-check: player_id={request.player_id}, ship_id={ship_id}, item_name={request.item_name!r}")

    try:
        async with get_db_session() as db:
            result = await equipment_service.equip_check(
                db,
                player_id=request.player_id,
                ship_id=ship_id,
                item_name=request.item_name,
            )
            return EquipCheckResponse(**result)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error in equip-check: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to perform equip check"
        ) from e


@router.post("/{ship_id}/equip", response_model=ShipResponse)
async def equip_item(
    ship_id: int,
    request: EquipItemRequest,
    equipment_service: EquipmentService = Depends(get_equipment_service),
):
    """Equip an item from the player's inventory onto a ship.

    Requires ``player_id`` in the request body.  Validates ownership, slot
    availability, and inventory possession before moving the item.
    """
    flogger.info(
        f"Equipping '{request.item_name}' ({request.equipment_type or 'auto'}) "
        f"for player {request.player_id} on ship {ship_id}"
    )

    try:
        # Package G (B.19): wrap in db.begin() so that ship-slot append and
        # inventory decrement are atomic (invariant I3).
        async with get_db_session() as db, db.begin():
            result = await equipment_service.equip_item(
                db,
                player_id=request.player_id,
                ship_id=ship_id,
                equipment_type=request.equipment_type,
                item_name=request.item_name,
            )
            ship = result["ship"]
            return ShipResponse(
                id=ship.id,
                player_id=ship.player_id,
                ship_name=ship.ship_name,
                nickname=ship.nickname,
                is_active=ship.is_active,
                weapons=ship.weapons,
                modules=ship.modules,
                turrets=ship.turrets,
                secondary_weapons=ship.secondary_weapons,
                created_at=ship.created_at.isoformat(),
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        flogger.error(f"Error equipping item: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to equip item",
        ) from e


@router.post("/{ship_id}/unequip", response_model=ShipResponse)
async def unequip_item(
    ship_id: int,
    request: UnequipItemRequest,
    equipment_service: EquipmentService = Depends(get_equipment_service),
):
    """Unequip an item from a ship back to the player's inventory.

    Requires ``player_id`` in the request body.  Validates ownership and that
    the item is currently equipped before returning it to inventory.
    """
    flogger.info(
        f"Unequipping '{request.item_name}' ({request.equipment_type or 'auto'}) "
        f"for player {request.player_id} from ship {ship_id}"
    )

    try:
        # Package G (B.19): wrap in db.begin() so that ship-slot remove and
        # inventory increment are atomic (invariant I3).
        async with get_db_session() as db, db.begin():
            result = await equipment_service.unequip_item(
                db,
                player_id=request.player_id,
                ship_id=ship_id,
                equipment_type=request.equipment_type,
                item_name=request.item_name,
            )
            ship = result["ship"]
            return ShipResponse(
                id=ship.id,
                player_id=ship.player_id,
                ship_name=ship.ship_name,
                nickname=ship.nickname,
                is_active=ship.is_active,
                weapons=ship.weapons,
                modules=ship.modules,
                turrets=ship.turrets,
                secondary_weapons=ship.secondary_weapons,
                created_at=ship.created_at.isoformat(),
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        flogger.error(f"Error unequipping item: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to unequip item",
        ) from e


@router.get("/{ship_id}/loadout", response_model=ShipLoadoutSummaryResponse)
async def get_ship_loadout(
    ship_id: int,
    player_ship_repo: PlayerShipRepository = Depends(get_player_ship_repository),
):
    """Get detailed loadout information for a ship."""
    flogger.debug(f"Getting loadout for ship {ship_id}")

    try:
        async with get_db_session() as db:
            loadout = await player_ship_repo.get_ship_loadout_summary(db, ship_id)

            return ShipLoadoutSummaryResponse(
                ship_id=loadout["ship_id"],
                ship_name=loadout["ship_name"],
                nickname=loadout["nickname"],
                is_active=loadout["is_active"],
                weapons=loadout["weapons"],
                modules=loadout["modules"],
                turrets=loadout["turrets"],
                secondary_weapons=loadout["secondary_weapons"],
                weapons_count=loadout["weapons_count"],
                modules_count=loadout["modules_count"],
                turrets_count=loadout["turrets_count"],
                secondary_weapons_count=loadout["secondary_weapons_count"],
            )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error getting ship loadout: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get ship loadout"
        ) from e


@router.delete("/{ship_id}")
async def delete_ship(
    ship_id: int,
    player_ship_repo: PlayerShipRepository = Depends(get_player_ship_repository),
):
    """Delete a ship."""
    flogger.warning(f"Deleting ship {ship_id}")

    try:
        async with get_db_session() as db:
            ship = await player_ship_repo.get_by_id(db, ship_id)
            if not ship:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Ship {ship_id} not found")

            # Don't allow deleting active ships
            if ship.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot delete active ship. Set another ship as active first.",
                )

            await player_ship_repo.remove(db, ship)

            return {"message": f"Ship {ship_id} deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error deleting ship {ship_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete ship") from e


@router.post("/transfer", response_model=TransferShipResponse)
async def transfer_ship(
    request: TransferShipRequest,
    ship_repo: PlayerShipRepository = Depends(get_player_ship_repository),
    player_repo: PlayerRepository = Depends(get_player_repository),
):
    """Transfer a PlayerShip from one player to another.

    Validates:
    - The ship exists and belongs to from_player
    - The ship is NOT the from_player's active ship
    - Both players exist

    Unequips all weapons/modules/turrets to from_player's inventory before transfer.
    Sets the transferred ship as inactive for to_player.
    """
    flogger.info(
        f"Transferring ship {request.ship_id} from player {request.from_player_id} to player {request.to_player_id}"
    )

    try:
        async with get_db_session() as db, db.begin():
            # Validate both players exist
            from_player = await player_repo.get_by_id(db, request.from_player_id)
            if not from_player:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Source player {request.from_player_id} not found",
                )

            to_player = await player_repo.get_by_id(db, request.to_player_id)
            if not to_player:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Target player {request.to_player_id} not found",
                )

            # Validate the ship exists and belongs to from_player
            ship = await ship_repo.get_by_id(db, request.ship_id)
            if not ship:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Ship {request.ship_id} not found",
                )

            if ship.player_id != request.from_player_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Ship {request.ship_id} does not belong to player {request.from_player_id}",
                )

            # Cannot give away active ship
            if ship.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot transfer the active ship. Set another ship as active first.",
                )

            # Package G (B.19): evacuate items via the LoadoutConsistencyService
            # choke-point (anti-duplication guard prevents legacy phantom-item
            # exploit on transfer).  The service clears the ship's slot lists
            # in the same transaction and returns the items moved to inventory.
            from services.loadout_consistency_service import LoadoutConsistencyService

            consistency = LoadoutConsistencyService()
            evac = await consistency.evacuate_ship_loadout_to_inventory(db, ship=ship)
            items_returned: list[str] = list(evac["items_returned"])

            # Transfer ship ownership to to_player (inactive)
            ship.player_id = request.to_player_id
            ship.is_active = False
            await db.flush()
            await db.refresh(ship)

            flogger.info(
                f"Ship {request.ship_id} ({ship.ship_name}) transferred from player "
                f"{request.from_player_id} to {request.to_player_id}. "
                f"Returned {len(items_returned)} items to source inventory."
            )

            return TransferShipResponse(
                ship_id=ship.id,
                ship_name=ship.ship_name,
                from_player_id=request.from_player_id,
                to_player_id=request.to_player_id,
                items_returned_to_source=items_returned,
                message=(
                    f"Ship '{ship.ship_name}' transferred from player {request.from_player_id} "
                    f"to player {request.to_player_id}. "
                    f"{len(items_returned)} item(s) returned to source inventory."
                ),
            )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error transferring ship {request.ship_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to transfer ship") from e
