"""
Ships API router for the BountyBot inventory system.

Handles REST API endpoints for ship management including ownership,
loadout management, and active ship selection.
"""


from fastapi import APIRouter, Depends, HTTPException, status
from persist.database.manager import get_db_session
from persist.repositories.player_repository import PlayerRepository
from persist.repositories.ship_repository import ShipRepository
from shared import bblogger

from api.schemas.ships_schema import (
    CreateShipRequest,
    EquipItemRequest,
    ShipLoadoutSummaryResponse,
    ShipResponse,
    UnequipItemRequest,
    UpdateLoadoutRequest,
    UpdateNicknameRequest,
)

flogger = bblogger.get_logger("ships-api-router")

router = APIRouter(
    prefix="/ships",
    tags=["ships"],
    responses={
        404: {"description": "Ship or player not found"},
        500: {"description": "Internal server error"}
    }
)

# Dependency injection
async def get_ship_repository():
    return ShipRepository()

async def get_player_repository():
    return PlayerRepository()

@router.get("/player/{player_id}", response_model=list[ShipResponse])
async def get_player_ships(
    player_id: int,
    ship_repo: ShipRepository = Depends(get_ship_repository)
):
    """Get all ships owned by a player."""
    flogger.debug(f"Getting ships for player {player_id}")

    try:
        async with get_db_session() as db:
            ships = await ship_repo.get_player_ships(db, player_id)

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
                    created_at=ship.created_at.isoformat()
                )
                for ship in ships
            ]

    except Exception as e:
        flogger.error(f"Error getting ships for player {player_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get player ships"
        ) from e

@router.get("/{ship_id}", response_model=ShipResponse)
async def get_ship(
    ship_id: int,
    ship_repo: ShipRepository = Depends(get_ship_repository)
):
    """Get a specific ship by ID."""
    flogger.debug(f"Getting ship {ship_id}")

    try:
        async with get_db_session() as db:
            ship = await ship_repo.get_by_id(db, ship_id)
            if not ship:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Ship {ship_id} not found"
                )

            return ShipResponse(
                id=ship.id,
                player_id=ship.player_id,
                ship_name=ship.ship_name,
                nickname=ship.nickname,
                is_active=ship.is_active,
                weapons=ship.weapons,
                modules=ship.modules,
                turrets=ship.turrets,
                created_at=ship.created_at.isoformat()
            )

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error getting ship {ship_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get ship"
        ) from e

@router.post("/", response_model=ShipResponse, status_code=status.HTTP_201_CREATED)
async def create_ship(
    request: CreateShipRequest,
    ship_repo: ShipRepository = Depends(get_ship_repository),
    player_repo: PlayerRepository = Depends(get_player_repository)
):
    """Create a new ship for a player."""
    flogger.info(f"Creating ship {request.ship_name} for player {request.player_id}")

    try:
        async with get_db_session() as db:
            # Verify player exists
            player = await player_repo.get_by_id(db, request.player_id)
            if not player:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Player {request.player_id} not found"
                )

            # Create ship
            ship_data = request.model_dump()
            ship = await ship_repo.create_or_update(db, ship_data)

            return ShipResponse(
                id=ship.id,
                player_id=ship.player_id,
                ship_name=ship.ship_name,
                nickname=ship.nickname,
                is_active=ship.is_active,
                weapons=ship.weapons,
                modules=ship.modules,
                turrets=ship.turrets,
                created_at=ship.created_at.isoformat()
            )

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error creating ship: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create ship"
        ) from e

@router.get("/player/{player_id}/active", response_model=ShipResponse | None)
async def get_active_ship(
    player_id: int,
    ship_repo: ShipRepository = Depends(get_ship_repository)
):
    """Get the active ship for a player."""
    flogger.debug(f"Getting active ship for player {player_id}")

    try:
        async with get_db_session() as db:
            ship = await ship_repo.get_active_ship(db, player_id)

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
                created_at=ship.created_at.isoformat()
            )

    except Exception as e:
        flogger.error(f"Error getting active ship for player {player_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get active ship"
        ) from e

@router.put("/{ship_id}/set-active", response_model=ShipResponse)
async def set_active_ship(
    ship_id: int,
    player_id: int,
    ship_repo: ShipRepository = Depends(get_ship_repository),
    player_repo: PlayerRepository = Depends(get_player_repository)
):
    """Set a ship as the active ship for a player."""
    flogger.info(f"Setting ship {ship_id} as active for player {player_id}")

    try:
        async with get_db_session() as db:
            ship = await ship_repo.set_active_ship(db, player_id, ship_id)

            # Update player's active ship reference
            await player_repo.update_active_ship(db, player_id, ship_id)

            return ShipResponse(
                id=ship.id,
                player_id=ship.player_id,
                ship_name=ship.ship_name,
                nickname=ship.nickname,
                is_active=ship.is_active,
                weapons=ship.weapons,
                modules=ship.modules,
                turrets=ship.turrets,
                created_at=ship.created_at.isoformat()
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error setting active ship: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to set active ship"
        ) from e

@router.put("/{ship_id}/loadout", response_model=ShipResponse)
async def update_ship_loadout(
    ship_id: int,
    request: UpdateLoadoutRequest,
    ship_repo: ShipRepository = Depends(get_ship_repository)
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

            ship = await ship_repo.update_loadout(db, ship_id, loadout_updates)

            return ShipResponse(
                id=ship.id,
                player_id=ship.player_id,
                ship_name=ship.ship_name,
                nickname=ship.nickname,
                is_active=ship.is_active,
                weapons=ship.weapons,
                modules=ship.modules,
                turrets=ship.turrets,
                created_at=ship.created_at.isoformat()
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error updating ship loadout: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update ship loadout"
        ) from e

@router.put("/{ship_id}/nickname", response_model=ShipResponse)
async def update_ship_nickname(
    ship_id: int,
    request: UpdateNicknameRequest,
    ship_repo: ShipRepository = Depends(get_ship_repository)
):
    """Update a ship's nickname."""
    flogger.info(f"Updating nickname for ship {ship_id}: {request.nickname}")

    try:
        async with get_db_session() as db:
            ship = await ship_repo.update_nickname(db, ship_id, request.nickname)

            return ShipResponse(
                id=ship.id,
                player_id=ship.player_id,
                ship_name=ship.ship_name,
                nickname=ship.nickname,
                is_active=ship.is_active,
                weapons=ship.weapons,
                modules=ship.modules,
                turrets=ship.turrets,
                created_at=ship.created_at.isoformat()
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error updating ship nickname: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update ship nickname"
        ) from e

@router.post("/{ship_id}/equip", response_model=ShipResponse)
async def equip_item(
    ship_id: int,
    request: EquipItemRequest,
    ship_repo: ShipRepository = Depends(get_ship_repository)
):
    """Equip an item to a ship."""
    flogger.info(f"Equipping {request.item_name} to {request.equipment_type} on ship {ship_id}")

    try:
        async with get_db_session() as db:
            ship = await ship_repo.add_equipment(
                db, ship_id, request.equipment_type, request.item_name
            )

            return ShipResponse(
                id=ship.id,
                player_id=ship.player_id,
                ship_name=ship.ship_name,
                nickname=ship.nickname,
                is_active=ship.is_active,
                weapons=ship.weapons,
                modules=ship.modules,
                turrets=ship.turrets,
                created_at=ship.created_at.isoformat()
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error equipping item: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to equip item"
        ) from e

@router.post("/{ship_id}/unequip", response_model=ShipResponse)
async def unequip_item(
    ship_id: int,
    request: UnequipItemRequest,
    ship_repo: ShipRepository = Depends(get_ship_repository)
):
    """Unequip an item from a ship."""
    flogger.info(f"Unequipping {request.item_name} from {request.equipment_type} on ship {ship_id}")

    try:
        async with get_db_session() as db:
            ship = await ship_repo.remove_equipment(
                db, ship_id, request.equipment_type, request.item_name
            )

            return ShipResponse(
                id=ship.id,
                player_id=ship.player_id,
                ship_name=ship.ship_name,
                nickname=ship.nickname,
                is_active=ship.is_active,
                weapons=ship.weapons,
                modules=ship.modules,
                turrets=ship.turrets,
                created_at=ship.created_at.isoformat()
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error unequipping item: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to unequip item"
        ) from e

@router.get("/{ship_id}/loadout", response_model=ShipLoadoutSummaryResponse)
async def get_ship_loadout(
    ship_id: int,
    ship_repo: ShipRepository = Depends(get_ship_repository)
):
    """Get detailed loadout information for a ship."""
    flogger.debug(f"Getting loadout for ship {ship_id}")

    try:
        async with get_db_session() as db:
            loadout = await ship_repo.get_ship_loadout_summary(db, ship_id)

            return ShipLoadoutSummaryResponse(
                ship_id=loadout["ship_id"],
                ship_name=loadout["ship_name"],
                nickname=loadout["nickname"],
                is_active=loadout["is_active"],
                weapons=loadout["weapons"],
                modules=loadout["modules"],
                turrets=loadout["turrets"],
                weapons_count=loadout["weapons_count"],
                modules_count=loadout["modules_count"],
                turrets_count=loadout["turrets_count"]
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error getting ship loadout: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get ship loadout"
        ) from e

@router.delete("/{ship_id}")
async def delete_ship(
    ship_id: int,
    ship_repo: ShipRepository = Depends(get_ship_repository)
):
    """Delete a ship."""
    flogger.warning(f"Deleting ship {ship_id}")

    try:
        async with get_db_session() as db:
            ship = await ship_repo.get_by_id(db, ship_id)
            if not ship:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Ship {ship_id} not found"
                )

            # Don't allow deleting active ships
            if ship.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot delete active ship. Set another ship as active first."
                )

            await ship_repo.remove(db, ship)

            return {"message": f"Ship {ship_id} deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error deleting ship {ship_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete ship"
        ) from e
