"""
Shops API router for the BountyBot inventory system.

Handles REST API endpoints for shop management including browsing,
purchasing, selling, and shop refresh operations.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from persist.database.manager import get_db_session
from services.shop_service import ShopService
from shared import bblogger

from api.schemas.shops_schema import (
    PurchaseRequest,
    RefreshShopRequest,
    SellRequest,
    ShopItemResponse,
    ShopSummaryResponse,
    TransactionResponse,
)

flogger = bblogger.get_logger("shops-api-router")

router = APIRouter(
    prefix="/shops",
    tags=["shops"],
    responses={
        404: {"description": "Shop or item not found"},
        500: {"description": "Internal server error"}
    }
)

# Dependency injection
async def get_shop_service():
    return ShopService()

@router.get("/guild/{guild_id}/tier/{tier}", response_model=list[ShopItemResponse])
async def get_shop_items(
    guild_id: int,
    tier: str,
    item_type: str | None = None,
    shop_service: ShopService = Depends(get_shop_service)
):
    """Get all items in a specific guild shop tier."""
    flogger.debug(f"Getting items from {tier} shop in guild {guild_id}, type filter: {item_type}")

    try:
        async with get_db_session() as db:
            items = await shop_service.get_shop_items(db, guild_id, tier, item_type)

            return [
                ShopItemResponse(
                    id=item.id,
                    guild_id=item.guild_id,
                    tier=item.tier,
                    tech_level=item.tech_level,
                    item_type=item.item_type,
                    item_name=item.item_name,
                    quantity=item.quantity,
                    price=item.price,
                    last_restocked=item.last_restocked.isoformat(),
                    refresh_interval_hours=item.refresh_interval_hours
                )
                for item in items
            ]

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error getting shop items: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get shop items"
        ) from e

@router.get("/guild/{guild_id}/summary", response_model=ShopSummaryResponse)
async def get_guild_shops_summary(
    guild_id: int,
    shop_service: ShopService = Depends(get_shop_service)
):
    """Get a summary of all shops for a guild."""
    flogger.debug(f"Getting shops summary for guild {guild_id}")

    try:
        async with get_db_session() as db:
            summary = await shop_service.shop_repo.get_guild_shops_summary(db, guild_id)

            return ShopSummaryResponse(
                guild_id=summary["guild_id"],
                total_items=summary["total_items"],
                shops=summary["shops"]
            )

    except Exception as e:
        flogger.error(f"Error getting shops summary: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get shops summary"
        ) from e

@router.post("/purchase", response_model=TransactionResponse)
async def purchase_item(
    request: PurchaseRequest,
    shop_service: ShopService = Depends(get_shop_service)
):
    """Purchase an item from a shop."""
    flogger.info(f"Player {request.player_id} purchasing {request.quantity} of shop item {request.shop_item_id}")

    try:
        async with get_db_session() as db:
            transaction = await shop_service.purchase_item(
                db, request.player_id, request.shop_item_id, request.quantity
            )

            return TransactionResponse(
                player_id=transaction["player_id"],
                item_type=transaction["item_type"],
                item_name=transaction["item_name"],
                quantity=transaction["quantity"],
                total_cost=transaction["total_cost"],
                remaining_credits=transaction["remaining_credits"],
                transaction_type="purchase"
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error processing purchase: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process purchase"
        ) from e

@router.post("/sell", response_model=TransactionResponse)
async def sell_item(
    request: SellRequest,
    shop_service: ShopService = Depends(get_shop_service)
):
    """Sell an item back to a shop."""
    flogger.info(
        f"Player {request.player_id} selling {request.quantity}x "
        f"{request.item_name} to {request.target_tier} shop"
    )

    try:
        async with get_db_session() as db:
            transaction = await shop_service.sell_item(
                db, request.player_id, request.item_type,
                request.item_name, request.quantity, request.target_tier
            )

            return TransactionResponse(
                player_id=transaction["player_id"],
                item_type=transaction["item_type"],
                item_name=transaction["item_name"],
                quantity=transaction["quantity"],
                total_value=transaction["total_sell_value"],
                remaining_credits=transaction["new_credits"],
                transaction_type="sale"
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error processing sale: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process sale"
        ) from e

@router.post("/refresh", response_model=dict[str, Any])
async def refresh_shop(
    request: RefreshShopRequest,
    shop_service: ShopService = Depends(get_shop_service)
):
    """Force refresh a shop's inventory."""
    flogger.info(f"Refreshing {request.tier} shop for guild {request.guild_id}")

    try:
        async with get_db_session() as db:
            refresh_details = await shop_service.refresh_shop(
                db, request.guild_id, request.tier, request.force_tech_level
            )

            return refresh_details

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error refreshing shop: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to refresh shop"
        ) from e

@router.get("/guild/{guild_id}/tier/{tier}/stats")
async def get_shop_statistics(
    guild_id: int,
    tier: str,
    shop_service: ShopService = Depends(get_shop_service)
):
    """Get detailed statistics for a specific shop."""
    flogger.debug(f"Getting statistics for {tier} shop in guild {guild_id}")

    try:
        async with get_db_session() as db:
            stats = await shop_service.shop_repo.get_shop_statistics(db, guild_id, tier)
            return stats

    except Exception as e:
        flogger.error(f"Error getting shop statistics: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get shop statistics"
        ) from e

@router.get("/guild/{guild_id}/tier/{tier}/tech-level/{tech_level}")
async def get_items_by_tech_level(
    guild_id: int,
    tier: str,
    tech_level: int,
    shop_service: ShopService = Depends(get_shop_service)
):
    """Get all items of a specific tech level from a shop."""
    flogger.debug(f"Getting tech level {tech_level} items from {tier} shop in guild {guild_id}")

    try:
        if tech_level < 1 or tech_level > 9:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tech level must be between 1 and 9"
            )

        async with get_db_session() as db:
            items = await shop_service.shop_repo.get_items_by_tech_level(
                db, guild_id, tier, tech_level
            )

            return [
                ShopItemResponse(
                    id=item.id,
                    guild_id=item.guild_id,
                    tier=item.tier,
                    tech_level=item.tech_level,
                    item_type=item.item_type,
                    item_name=item.item_name,
                    quantity=item.quantity,
                    price=item.price,
                    last_restocked=item.last_restocked.isoformat(),
                    refresh_interval_hours=item.refresh_interval_hours
                )
                for item in items
            ]

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error getting items by tech level: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get items by tech level"
        ) from e

@router.get("/guild/{guild_id}/refresh-status")
async def get_refresh_status(
    guild_id: int,
    shop_service: ShopService = Depends(get_shop_service)
):
    """Get refresh status for all shops in a guild."""
    flogger.debug(f"Getting refresh status for guild {guild_id}")

    try:
        async with get_db_session() as db:
            due_items = await shop_service.shop_repo.get_items_due_for_refresh(db, guild_id)

            # Group by tier
            due_by_tier = {}
            for item in due_items:
                if item.tier not in due_by_tier:
                    due_by_tier[item.tier] = 0
                due_by_tier[item.tier] += 1

            return {
                "guild_id": guild_id,
                "total_items_due_for_refresh": len(due_items),
                "due_by_tier": due_by_tier,
                "needs_refresh": len(due_items) > 0
            }

    except Exception as e:
        flogger.error(f"Error getting refresh status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get refresh status"
        ) from e

@router.get("/item/{shop_item_id}")
async def get_shop_item(
    shop_item_id: int,
    shop_service: ShopService = Depends(get_shop_service)
):
    """Get details for a specific shop item."""
    flogger.debug(f"Getting shop item {shop_item_id}")

    try:
        async with get_db_session() as db:
            item = await shop_service.shop_repo.get_by_id(db, shop_item_id)
            if not item:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Shop item {shop_item_id} not found"
                )

            return ShopItemResponse(
                id=item.id,
                guild_id=item.guild_id,
                tier=item.tier,
                tech_level=item.tech_level,
                item_type=item.item_type,
                item_name=item.item_name,
                quantity=item.quantity,
                price=item.price,
                last_restocked=item.last_restocked.isoformat(),
                refresh_interval_hours=item.refresh_interval_hours
            )

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error getting shop item: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get shop item"
        ) from e

@router.put("/guild/{guild_id}/prices")
async def update_shop_prices(
    guild_id: int,
    price_multiplier: float = Query(..., gt=0, description="Price multiplier (e.g., 1.1 for 10% increase)"),
    shop_service: ShopService = Depends(get_shop_service)
):
    """Update all shop prices for a guild by a multiplier."""
    flogger.info(f"Updating shop prices for guild {guild_id} with multiplier {price_multiplier}")

    try:
        async with get_db_session() as db:
            updated_count = await shop_service.shop_repo.update_prices(db, guild_id, price_multiplier)

            return {
                "guild_id": guild_id,
                "price_multiplier": price_multiplier,
                "items_updated": updated_count,
                "message": f"Updated prices for {updated_count} shop items"
            }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error updating shop prices: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update shop prices"
        ) from e
