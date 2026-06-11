"""
Inventory API router for the BountyBot inventory system.

Handles REST API endpoints for inventory management operations.
This router follows the requirement that all major subsystem interactions
must be done via REST API.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from persist.database.manager import get_db_session
from persist.repositories.player_repository import PlayerRepository
from services.exceptions import InvalidItemTypeError
from services.inventory_service import InventoryService
from shared import bblogger

from api.schemas.inventory_schema import (
    AddItemRequest,
    InventoryItemResponse,
    InventorySummaryResponse,
    ItemTransactionResponse,
    RemoveItemRequest,
    TransferItemRequest,
)

flogger = bblogger.get_logger("inventory-api-router")

router = APIRouter(
    prefix="/inventory",
    tags=["inventory"],
    responses={404: {"description": "Item or player not found"}, 500: {"description": "Internal server error"}},
)


# Dependency injection
async def get_inventory_service():
    return InventoryService()


async def get_player_repository():
    return PlayerRepository()


@router.get("/player/{player_id}", response_model=list[InventoryItemResponse])
async def get_player_inventory(
    player_id: int,
    item_type: str | None = None,
    include_ships: bool = False,
    inventory_service: InventoryService = Depends(get_inventory_service),
):
    """
    Get a player's inventory, optionally filtered by item type.

    ``include_ships=true`` additionally lists the player's inactive ships
    (the active ship counts as equipped and is excluded). Default false so
    autocomplete/search/count consumers see cargo items only.
    """
    flogger.debug(f"Getting inventory for player {player_id}, type filter: {item_type}, include_ships={include_ships}")

    try:
        async with get_db_session() as db:
            items = await inventory_service.get_player_inventory(db, player_id, item_type, include_ships=include_ships)

            return [
                InventoryItemResponse(
                    id=item["id"],
                    item_type=item["item_type"],
                    item_name=item["item_name"],
                    quantity=item["quantity"],
                    acquired_at=item["acquired_at"],
                    item_details=item["item_details"],
                )
                for item in items
            ]

    except InvalidItemTypeError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error getting inventory for player {player_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get inventory") from e


@router.get("/player/{player_id}/summary", response_model=InventorySummaryResponse)
async def get_inventory_summary(
    player_id: int,
    include_ships: bool = False,
    inventory_service: InventoryService = Depends(get_inventory_service),
):
    """Get a summary of a player's inventory by item type.

    ``include_ships=true`` adds the inactive-ship count to ``ship`` and
    ``total_items`` (the active ship counts as equipped and is excluded).
    """
    flogger.debug(f"Getting inventory summary for player {player_id}, include_ships={include_ships}")

    try:
        async with get_db_session() as db:
            summary = await inventory_service.get_inventory_summary(db, player_id, include_ships=include_ships)

            return InventorySummaryResponse(**summary)

    except InvalidItemTypeError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error getting inventory summary for player {player_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get inventory summary"
        ) from e


@router.post("/add", response_model=ItemTransactionResponse)
async def add_item_to_inventory(
    request: AddItemRequest, inventory_service: InventoryService = Depends(get_inventory_service)
):
    """Add items to a player's inventory."""
    flogger.info(f"Adding {request.quantity}x {request.item_name} to player {request.player_id}")

    try:
        async with get_db_session() as db:
            result = await inventory_service.add_item_to_inventory(
                db, request.player_id, request.item_type, request.item_name, request.quantity
            )

            return ItemTransactionResponse(
                player_id=result["player_id"],
                item_type=result["item_type"],
                item_name=result["item_name"],
                quantity_changed=result["quantity_added"],
                new_total_quantity=result["new_total_quantity"],
                transaction_time=result["transaction_time"],
            )

    except InvalidItemTypeError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error adding item to inventory: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add item to inventory"
        ) from e


@router.post("/remove", response_model=ItemTransactionResponse)
async def remove_item_from_inventory(
    request: RemoveItemRequest, inventory_service: InventoryService = Depends(get_inventory_service)
):
    """Remove items from a player's inventory."""
    flogger.info(f"Removing {request.quantity}x {request.item_name} from player {request.player_id}")

    try:
        async with get_db_session() as db:
            result = await inventory_service.remove_item_from_inventory(
                db, request.player_id, request.item_type, request.item_name, request.quantity
            )

            return ItemTransactionResponse(
                player_id=result["player_id"],
                item_type=result["item_type"],
                item_name=result["item_name"],
                quantity_changed=-result["quantity_removed"],
                new_total_quantity=result["new_quantity"],
                transaction_time=None,  # Remove operations don't have acquisition time
            )

    except InvalidItemTypeError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error removing item from inventory: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to remove item from inventory"
        ) from e


@router.post("/transfer", response_model=dict[str, Any])
async def transfer_item_between_players(
    request: TransferItemRequest, inventory_service: InventoryService = Depends(get_inventory_service)
):
    """Transfer items between players (for future trading system)."""
    flogger.info(
        f"Transferring {request.quantity}x {request.item_name} "
        f"from player {request.from_player_id} to {request.to_player_id}"
    )

    try:
        async with get_db_session() as db, db.begin():
            result = await inventory_service.transfer_item_between_players(
                db, request.from_player_id, request.to_player_id, request.item_type, request.item_name, request.quantity
            )

            return result

    except InvalidItemTypeError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error transferring item: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to transfer item") from e


@router.get("/player/{player_id}/search", response_model=list[InventoryItemResponse])
async def search_inventory(
    player_id: int, q: str, inventory_service: InventoryService = Depends(get_inventory_service)
):
    """Search player's inventory for items matching a search term."""
    flogger.debug(f"Searching inventory for player {player_id} with term: {q}")

    try:
        async with get_db_session() as db:
            items = await inventory_service.search_inventory(db, player_id, q)

            return [
                InventoryItemResponse(
                    id=item["id"],
                    item_type=item["item_type"],
                    item_name=item["item_name"],
                    quantity=item["quantity"],
                    acquired_at=item["acquired_at"],
                    item_details=item["item_details"],
                )
                for item in items
            ]

    except InvalidItemTypeError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error searching inventory: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to search inventory"
        ) from e


@router.get("/player/{player_id}/item/{item_name}/count")
async def get_item_count(
    player_id: int,
    item_name: str,
    item_type: str | None = None,
    inventory_service: InventoryService = Depends(get_inventory_service),
):
    """Get the quantity of a specific item a player owns.

    If item_type is not provided, the server resolves it from the player's
    inventory by looking up the item_name. Returns the concrete type and quantity.
    """
    flogger.debug(f"Getting count of item {item_name} for player {player_id}, item_type={item_type}")

    try:
        async with get_db_session() as db:
            # If item_type not provided, resolve from inventory
            resolved_item_type = item_type
            if not resolved_item_type:
                # Get the player's inventory and find the item's type
                items = await inventory_service.get_player_inventory(db, player_id)
                matching_items = [item for item in items if item.get("item_name") == item_name]
                if matching_items:
                    resolved_item_type = matching_items[0].get("item_type")
                if not resolved_item_type:
                    raise ValueError(f"Item '{item_name}' not found in player's inventory")

            count = await inventory_service.get_player_item_count(db, player_id, resolved_item_type, item_name)

            return {"player_id": player_id, "item_type": resolved_item_type, "item_name": item_name, "quantity": count}

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error getting item count: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get item count") from e


@router.get("/player/{player_id}/validate/{ship_name}/{item_name}")
async def validate_item_compatibility(
    player_id: int,
    ship_name: str,
    item_name: str,
    item_type: str,
    inventory_service: InventoryService = Depends(get_inventory_service),
):
    """Validate if an item can be equipped on a specific ship."""
    flogger.debug(f"Validating compatibility of {item_name} with {ship_name} for player {player_id}")

    try:
        async with get_db_session() as db:
            compatibility = await inventory_service.validate_item_compatibility(
                db, player_id, ship_name, item_type, item_name
            )

            return compatibility

    except Exception as e:
        flogger.error(f"Error validating item compatibility: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to validate item compatibility"
        ) from e


@router.post("/player/{player_id}/consolidate")
async def consolidate_inventory(
    player_id: int,
    inventory_service: InventoryService = Depends(get_inventory_service),
    player_repo: PlayerRepository = Depends(get_player_repository),
):
    """Consolidate duplicate inventory entries (maintenance function)."""
    flogger.info(f"Consolidating inventory for player {player_id}")

    try:
        # D5-T3 (path 18): consolidate is a multi-row read-modify-write across
        # player_inventories (read all cargo rows → merge dup groups → delete +
        # update_quantity). It previously ran with NEITHER a transaction NOR a
        # lock, so two same-player consolidations (or a consolidation racing a
        # cargo decrement) could interleave and LOSE an update.
        #
        # The LOCK is what fixes that: acquiring the aggregate-root Player row
        # FOR UPDATE FIRST (lock-ordering rule: aggregate row before any read
        # that feeds the RMW) serialises concurrent same-player RMWs, so the
        # lost update cannot occur. AsyncSession autobegin already holds that
        # row lock until the session is committed/closed, so db.begin() does NOT
        # change the lock's duration.
        #
        # db.begin() is load-bearing for ATOMICITY: it makes the Player lock
        # acquisition and the flush-only (commit=False) consolidate writes one
        # explicit unit of work that commits together on success and rolls back
        # together on error. (Durability is additionally backstopped by
        # get_db_session's AC-7 auto-commit-on-clean-exit, but the project's
        # transaction-discipline contract requires the boundary to be explicit
        # rather than relying on that safety net — see test_transaction_discipline.)
        # The service therefore runs with commit=False so this db.begin() owns
        # the transaction.
        async with get_db_session() as db, db.begin():
            await player_repo.get_by_id_for_update(db, player_id)
            result = await inventory_service.consolidate_inventory(db, player_id, commit=False)

            return result

    except Exception as e:
        flogger.error(f"Error consolidating inventory: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to consolidate inventory"
        ) from e
