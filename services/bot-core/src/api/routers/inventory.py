"""
Inventory API router for the BountyBot inventory system.

Handles REST API endpoints for inventory management operations.
This router follows the requirement that all major subsystem interactions
must be done via REST API.
"""

from fastapi import APIRouter, HTTPException, Depends, status
from typing import List, Optional, Dict, Any
import shared.bblogger as bblogger
from persist.database.manager import get_db_session
from services.inventory_service import InventoryService

flogger = bblogger.get_logger("inventory-api-router")

router = APIRouter(
    prefix="/inventory",
    tags=["inventory"],
    responses={
        404: {"description": "Item or player not found"},
        500: {"description": "Internal server error"}
    }
)

# Import response models from schemas
from api.schemas.inventory_schema import (
    InventoryItemResponse,
    InventorySummaryResponse,
    AddItemRequest,
    RemoveItemRequest,
    TransferItemRequest,
    ItemTransactionResponse
)

# Dependency injection
async def get_inventory_service():
    return InventoryService()

@router.get("/player/{player_id}", response_model=List[InventoryItemResponse])
async def get_player_inventory(
    player_id: int,
    item_type: Optional[str] = None,
    inventory_service: InventoryService = Depends(get_inventory_service)
):
    """
    Get a player's inventory, optionally filtered by item type.
    """
    flogger.debug(f"Getting inventory for player {player_id}, type filter: {item_type}")
    
    try:
        async with get_db_session() as db:
            items = await inventory_service.get_player_inventory(db, player_id, item_type)
            
            return [
                InventoryItemResponse(
                    id=item["id"],
                    item_type=item["item_type"],
                    item_name=item["item_name"],
                    quantity=item["quantity"],
                    acquired_at=item["acquired_at"],
                    item_details=item["item_details"]
                )
                for item in items
            ]
            
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        flogger.error(f"Error getting inventory for player {player_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get inventory"
        )

@router.get("/player/{player_id}/summary", response_model=InventorySummaryResponse)
async def get_inventory_summary(
    player_id: int,
    inventory_service: InventoryService = Depends(get_inventory_service)
):
    """Get a summary of a player's inventory by item type."""
    flogger.debug(f"Getting inventory summary for player {player_id}")
    
    try:
        async with get_db_session() as db:
            summary = await inventory_service.get_inventory_summary(db, player_id)
            
            return InventorySummaryResponse(
                player_id=summary["player_id"],
                player_tier=summary["player_tier"],
                guild_id=summary["guild_id"],
                ship=summary["ship"],
                weapon=summary["weapon"],
                module=summary["module"],
                turret=summary["turret"],
                total_items=summary["total_items"]
            )
            
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        flogger.error(f"Error getting inventory summary for player {player_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get inventory summary"
        )

@router.post("/add", response_model=ItemTransactionResponse)
async def add_item_to_inventory(
    request: AddItemRequest,
    inventory_service: InventoryService = Depends(get_inventory_service)
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
                transaction_time=result["transaction_time"]
            )
            
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        flogger.error(f"Error adding item to inventory: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add item to inventory"
        )

@router.post("/remove", response_model=ItemTransactionResponse)
async def remove_item_from_inventory(
    request: RemoveItemRequest,
    inventory_service: InventoryService = Depends(get_inventory_service)
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
                transaction_time=None  # Remove operations don't have acquisition time
            )
            
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        flogger.error(f"Error removing item from inventory: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to remove item from inventory"
        )

@router.post("/transfer", response_model=Dict[str, Any])
async def transfer_item_between_players(
    request: TransferItemRequest,
    inventory_service: InventoryService = Depends(get_inventory_service)
):
    """Transfer items between players (for future trading system)."""
    flogger.info(f"Transferring {request.quantity}x {request.item_name} from player {request.from_player_id} to {request.to_player_id}")
    
    try:
        async with get_db_session() as db:
            result = await inventory_service.transfer_item_between_players(
                db, 
                request.from_player_id, 
                request.to_player_id, 
                request.item_type, 
                request.item_name, 
                request.quantity
            )
            
            return result
            
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        flogger.error(f"Error transferring item: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to transfer item"
        )

@router.get("/player/{player_id}/search", response_model=List[InventoryItemResponse])
async def search_inventory(
    player_id: int,
    q: str,
    inventory_service: InventoryService = Depends(get_inventory_service)
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
                    item_details=item["item_details"]
                )
                for item in items
            ]
            
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        flogger.error(f"Error searching inventory: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to search inventory"
        )

@router.get("/player/{player_id}/item/{item_name}/count")
async def get_item_count(
    player_id: int,
    item_name: str,
    item_type: str,
    inventory_service: InventoryService = Depends(get_inventory_service)
):
    """Get the quantity of a specific item a player owns."""
    flogger.debug(f"Getting count of item {item_name} for player {player_id}")
    
    try:
        async with get_db_session() as db:
            count = await inventory_service.get_player_item_count(
                db, player_id, item_type, item_name
            )
            
            return {
                "player_id": player_id,
                "item_type": item_type,
                "item_name": item_name,
                "quantity": count
            }
            
    except Exception as e:
        flogger.error(f"Error getting item count: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get item count"
        )

@router.get("/player/{player_id}/validate/{ship_name}/{item_name}")
async def validate_item_compatibility(
    player_id: int,
    ship_name: str,
    item_name: str,
    item_type: str,
    inventory_service: InventoryService = Depends(get_inventory_service)
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
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to validate item compatibility"
        )

@router.post("/player/{player_id}/consolidate")
async def consolidate_inventory(
    player_id: int,
    inventory_service: InventoryService = Depends(get_inventory_service)
):
    """Consolidate duplicate inventory entries (maintenance function)."""
    flogger.info(f"Consolidating inventory for player {player_id}")
    
    try:
        async with get_db_session() as db:
            result = await inventory_service.consolidate_inventory(db, player_id)
            
            return result
            
    except Exception as e:
        flogger.error(f"Error consolidating inventory: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to consolidate inventory"
        )