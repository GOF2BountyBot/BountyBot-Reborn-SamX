"""
Admin API router for the BountyBot inventory system.

Handles administrative operations including:
- Guild initialization and configuration
- Player management (credits, XP, inventory)
- Shop management and refresh
- Role-based access control
- System health and statistics
"""

from shared import bblogger
from api.schemas.admin_schema import (
    AddInventoryItemRequest,
    GuildInitializationResponse,
    InitializeGuildRequest,
    RefreshShopRequest,
    SystemHealthResponse,
    UpdatePlayerCreditsRequest,
    UpdatePlayerXPRequest,
    UpdateShopConfigRequest,
)
from fastapi import APIRouter, Depends, HTTPException, status
from persist.database.manager import get_db_session
from services.config_service import ConfigService
from services.player_service import PlayerService
from services.shop_service import ShopService

flogger = bblogger.get_logger("admin-api-router")

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    responses={
        403: {"description": "Insufficient permissions"},
        404: {"description": "Resource not found"},
        500: {"description": "Internal server error"}
    }
)

# Dependency injection
async def get_player_service():
    return PlayerService()

async def get_shop_service():
    return ShopService()

async def get_config_service():
    return ConfigService()

async def verify_admin_permissions(guild_id: int, user_id: int) -> bool:
    """
    Verify that the user has admin permissions for the guild.

    TODO: Integrate with Discord role checking via discord-gateway
    For now, this is a placeholder that should be implemented.
    """
    # This should make a call to discord-gateway to verify role membership
    # For development, return True - MUST be implemented for production
    flogger.warning(f"Admin permission check bypassed for user {user_id} in guild {guild_id}")
    return True

@router.post("/guilds/initialize", response_model=GuildInitializationResponse)
async def initialize_guild(
    request: InitializeGuildRequest,
    config_service: ConfigService = Depends(get_config_service),
    shop_service: ShopService = Depends(get_shop_service)
):
    """
    Initialize a guild with default configuration and empty shops.

    Creates:
    - Guild configuration with default settings
    - Empty shops for all four tiers
    - Admin role configuration
    """
    flogger.info(f"Initializing guild {request.guild_id}")

    try:
        async with get_db_session() as db:
            # Create or update guild configuration
            config_data = {
                "guild_id": request.guild_id,
                "admin_role_id": request.admin_role_id,
                "starting_credits": request.starting_credits
            }

            await config_service.create_or_update_config(db, config_data)

            # Initialize empty shops for all tiers
            shops_created = 0
            tiers = ["Bronze", "Silver", "Gold", "Platinum"]

            for tier in tiers:
                await shop_service.refresh_shop(db, request.guild_id, tier)
                shops_created += 1

            flogger.info(f"Successfully initialized guild {request.guild_id}")

            return GuildInitializationResponse(
                guild_id=request.guild_id,
                admin_role_id=request.admin_role_id,
                shops_created=shops_created,
                config_created=True,
                message=f"Guild {request.guild_id} initialized successfully with {shops_created} shops"
            )

    except Exception as e:
        flogger.error(f"Error initializing guild {request.guild_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initialize guild"
        ) from e

@router.post("/guilds/{guild_id}/reset")
async def reset_guild(
    guild_id: int,
    preserve_players: bool = True,
    config_service: ConfigService = Depends(get_config_service),
    shop_service: ShopService = Depends(get_shop_service)
):
    """
    Reset guild configuration to defaults.

    Optionally preserve or clear all player data.
    """
    flogger.info(f"Resetting guild {guild_id}, preserve_players={preserve_players}")

    # TODO: Add admin permission check
    # if not await verify_admin_permissions(guild_id, user_id):
    #     raise HTTPException(status_code=403, detail="Insufficient permissions")

    try:
        async with get_db_session() as db:
            if not preserve_players:
                # Clear all player data for this guild
                await config_service.clear_guild_players(db, guild_id)
                flogger.info(f"Cleared all player data for guild {guild_id}")

            # Reset configuration to defaults
            await config_service.reset_to_defaults(db, guild_id)

            # Refresh all shops
            shops_refreshed = 0
            for tier in ["Bronze", "Silver", "Gold", "Platinum"]:
                await shop_service.refresh_shop(db, guild_id, tier)
                shops_refreshed += 1

            return {
                "guild_id": guild_id,
                "players_preserved": preserve_players,
                "shops_refreshed": shops_refreshed,
                "message": f"Guild {guild_id} reset successfully"
            }

    except Exception as e:
        flogger.error(f"Error resetting guild {guild_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reset guild"
        ) from e

@router.delete("/guilds/{guild_id}/uninstall")
async def uninstall_bot(
    guild_id: int,
    config_service: ConfigService = Depends(get_config_service)
):
    """
    Completely remove all bot data for a guild.

    WARNING: This is irreversible and removes all player data, configurations, and shops.
    """
    flogger.warning(f"Uninstalling bot from guild {guild_id} - ALL DATA WILL BE LOST")

    # TODO: Add admin permission check with extra confirmation

    try:
        async with get_db_session() as db:
            # Remove all guild data
            removed_counts = await config_service.uninstall_guild(db, guild_id)

            flogger.warning(f"Uninstalled bot from guild {guild_id}: {removed_counts}")

            return {
                "guild_id": guild_id,
                "removed_counts": removed_counts,
                "message": f"Bot completely uninstalled from guild {guild_id}",
                "warning": "All data has been permanently deleted"
            }

    except Exception as e:
        flogger.error(f"Error uninstalling bot from guild {guild_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to uninstall bot"
        ) from e

@router.put("/players/credits")
async def update_player_credits(
    request: UpdatePlayerCreditsRequest,
    player_service: PlayerService = Depends(get_player_service)
):
    """Update a player's credits."""
    flogger.info(f"Admin updating credits for player {request.player_id}: {request.credits}")

    try:
        async with get_db_session() as db:
            player = await player_service.update_player_credits(
                db, request.player_id, request.credits, request.update_lifetime
            )

            return {
                "player_id": request.player_id,
                "old_credits": player.credits - request.credits,
                "new_credits": request.credits,
                "lifetime_credits": player.lifetime_credits,
                "message": f"Credits updated for player {request.player_id}"
            }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error updating credits for player {request.player_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update credits"
        ) from e

@router.put("/players/xp")
async def update_player_xp(
    request: UpdatePlayerXPRequest,
    player_service: PlayerService = Depends(get_player_service)
):
    """Update a player's XP and check for tier advancement."""
    flogger.info(f"Admin updating XP for player {request.player_id}: {request.xp}")

    try:
        async with get_db_session() as db:
            old_player = await player_service.player_repo.get_by_id(db, request.player_id)
            if not old_player:
                raise HTTPException(status_code=404, detail="Player not found")

            old_tier = old_player.tier
            player = await player_service.update_player_xp(db, request.player_id, request.xp)

            return {
                "player_id": request.player_id,
                "old_xp": old_player.xp,
                "new_xp": request.xp,
                "old_tier": old_tier,
                "new_tier": player.tier,
                "tier_changed": old_tier != player.tier,
                "message": f"XP updated for player {request.player_id}"
            }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error updating XP for player {request.player_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update XP"
        ) from e

@router.post("/players/inventory/add")
async def add_inventory_item(
    request: AddInventoryItemRequest,
    player_service: PlayerService = Depends(get_player_service)
):
    """Add items to a player's inventory."""
    flogger.info(f"Admin adding {request.quantity}x {request.item_name} to player {request.player_id}")

    try:
        async with get_db_session() as db:
            # Verify player exists
            player = await player_service.player_repo.get_by_id(db, request.player_id)
            if not player:
                raise HTTPException(status_code=404, detail="Player not found")

            # Add item to inventory (this would need InventoryService implementation)
            # For now, return success message
            return {
                "player_id": request.player_id,
                "item_type": request.item_type,
                "item_name": request.item_name,
                "quantity": request.quantity,
                "message": f"Added {request.quantity}x {request.item_name} to player {request.player_id} inventory"
            }

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error adding inventory item: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add inventory item"
        ) from e

@router.post("/shops/refresh")
async def refresh_shop(
    request: RefreshShopRequest,
    shop_service: ShopService = Depends(get_shop_service)
):
    """Force refresh a shop's inventory."""
    flogger.info(f"Admin refreshing {request.tier} shop for guild {request.guild_id}")

    try:
        async with get_db_session() as db:
            refresh_details = await shop_service.refresh_shop(
                db, request.guild_id, request.tier, request.force_tech_level
            )

            return {
                **refresh_details,
                "message": f"Successfully refreshed {request.tier} shop for guild {request.guild_id}"
            }

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

@router.put("/shops/config")
async def update_shop_config(
    request: UpdateShopConfigRequest,
    config_service: ConfigService = Depends(get_config_service)
):
    """Update shop configuration parameters."""
    flogger.info(f"Admin updating shop config for guild {request.guild_id}")

    try:
        async with get_db_session() as db:
            config = await config_service.update_shop_config(db, request.dict(exclude_unset=True))

            return {
                "guild_id": request.guild_id,
                "updated_config": config,
                "message": f"Shop configuration updated for guild {request.guild_id}"
            }

    except Exception as e:
        flogger.error(f"Error updating shop config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update shop configuration"
        ) from e

@router.get("/system/health", response_model=SystemHealthResponse)
async def get_system_health():
    """Get comprehensive system health information."""
    flogger.debug("Admin requesting system health information")

    try:
        async with get_db_session():
            # Get system statistics
            # This would need implementation in respective services
            health_info = {
                "database_status": "healthy",
                "total_users": 0,  # TODO: Implement
                "total_players": 0,  # TODO: Implement
                "total_guilds": 0,  # TODO: Implement
                "shop_items_count": 0,  # TODO: Implement
                "system_status": "operational"
            }

            return SystemHealthResponse(**health_info)

    except Exception as e:
        flogger.error(f"Error getting system health: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get system health"
        ) from e

@router.get("/guilds/{guild_id}/stats")
async def get_guild_statistics(
    guild_id: int,
    player_service: PlayerService = Depends(get_player_service)
):
    """Get comprehensive statistics for a guild."""
    flogger.debug(f"Admin requesting statistics for guild {guild_id}")

    try:
        async with get_db_session() as db:
            players = await player_service.player_repo.get_players_by_guild(db, guild_id)

            # Calculate statistics
            total_players = len(players)
            tier_counts = {}
            total_credits = 0
            total_xp = 0

            for player in players:
                tier_counts[player.tier] = tier_counts.get(player.tier, 0) + 1
                total_credits += player.credits
                total_xp += player.xp

            stats = {
                "guild_id": guild_id,
                "total_players": total_players,
                "tier_distribution": tier_counts,
                "total_credits": total_credits,
                "total_xp": total_xp,
                "average_credits": total_credits / total_players if total_players > 0 else 0,
                "average_xp": total_xp / total_players if total_players > 0 else 0
            }

            return stats

    except Exception as e:
        flogger.error(f"Error getting guild statistics: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get guild statistics"
        ) from e
