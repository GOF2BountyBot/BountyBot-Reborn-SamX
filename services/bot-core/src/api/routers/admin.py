"""
Admin API router for the BountyBot inventory system.

Handles administrative operations including:
- Guild initialization and configuration
- Player management (credits, XP, inventory)
- Shop management and refresh
- Role-based access control
- System health and statistics
"""

import os

from fastapi import APIRouter, Depends, HTTPException, status
from persist.database.manager import get_db_session
from services.audit_service import AuditService
from services.config_service import ConfigService
from services.inventory_service import InventoryService
from services.player_service import PlayerService
from services.shop_service import ShopService
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

flogger = bblogger.get_logger("admin-api-router")

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    responses={
        403: {"description": "Insufficient permissions"},
        404: {"description": "Resource not found"},
        500: {"description": "Internal server error"},
    },
)


# Dependency injection
async def get_player_service():
    return PlayerService()


async def get_shop_service():
    return ShopService()


async def get_config_service():
    return ConfigService()


async def get_inventory_service():
    return InventoryService()


async def verify_admin_permissions(guild_id: int, user_id: int) -> bool:
    """
    Verify that the user has admin permissions for the guild.

    Checks if the user_id is in the ADMIN_USER_IDS environment variable
    (comma-separated list of Discord user IDs).

    If ADMIN_USER_IDS is not set or empty, falls back to allowing all access
    (development mode) with a warning log.
    """
    admin_ids_raw = os.environ.get("ADMIN_USER_IDS", "").strip()
    if not admin_ids_raw:
        flogger.warning(
            f"ADMIN_USER_IDS not configured - allowing access for user {user_id} in guild {guild_id} (dev mode)"
        )
        return True
    admin_ids = {int(uid.strip()) for uid in admin_ids_raw.split(",") if uid.strip()}
    if user_id in admin_ids:
        return True
    flogger.warning(f"Permission denied: user {user_id} is not in ADMIN_USER_IDS for guild {guild_id}")
    return False


@router.post("/guilds/initialize", response_model=GuildInitializationResponse)
async def initialize_guild(
    request: InitializeGuildRequest,
    user_id: int,
    config_service: ConfigService = Depends(get_config_service),
    shop_service: ShopService = Depends(get_shop_service),
):
    """
    Initialize a guild with default configuration and empty shops.

    Creates:
    - Guild configuration with default settings
    - Empty shops for all four tiers
    - Admin role configuration

    Requires admin permissions (user_id must be in ADMIN_USER_IDS).
    """
    if not await verify_admin_permissions(request.guild_id, user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")

    flogger.info(f"Initializing guild {request.guild_id}")

    try:
        async with get_db_session() as db:
            # Create or update guild configuration
            config_data = {
                "guild_id": request.guild_id,
                "admin_role_id": request.admin_role_id,
                "starting_credits": request.starting_credits,
            }

            await config_service.create_or_update_config(db, config_data)

            # Initialize empty shops for all tiers
            shops_created = 0
            tiers = ["Bronze", "Silver", "Gold", "Platinum"]

            for tier in tiers:
                await shop_service.refresh_shop(db, request.guild_id, tier)
                shops_created += 1

            flogger.info(f"Successfully initialized guild {request.guild_id}")

            await AuditService.log_action(
                db,
                user_id=user_id,
                action="guild_initialize",
                guild_id=request.guild_id,
                resource_type="guild",
                resource_id=str(request.guild_id),
                details={"admin_role_id": request.admin_role_id, "shops_created": shops_created},
            )

            return GuildInitializationResponse(
                guild_id=request.guild_id,
                admin_role_id=request.admin_role_id,
                shops_created=shops_created,
                config_created=True,
                message=f"Guild {request.guild_id} initialized successfully with {shops_created} shops",
            )

    except Exception as e:
        flogger.error(f"Error initializing guild {request.guild_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to initialize guild"
        ) from e


@router.post("/guilds/{guild_id}/reset")
async def reset_guild(
    guild_id: int,
    user_id: int,
    preserve_players: bool = True,
    config_service: ConfigService = Depends(get_config_service),
    shop_service: ShopService = Depends(get_shop_service),
):
    """
    Reset guild configuration to defaults.

    Optionally preserve or clear all player data.
    Requires admin permissions (user_id must be in ADMIN_USER_IDS).
    """
    flogger.info(f"Resetting guild {guild_id}, preserve_players={preserve_players}")

    if not await verify_admin_permissions(guild_id, user_id):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

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

            await AuditService.log_action(
                db,
                user_id=user_id,
                action="guild_reset",
                guild_id=guild_id,
                resource_type="guild",
                resource_id=str(guild_id),
                details={"preserve_players": preserve_players},
            )

            return {
                "guild_id": guild_id,
                "players_preserved": preserve_players,
                "shops_refreshed": shops_refreshed,
                "message": f"Guild {guild_id} reset successfully",
            }

    except Exception as e:
        flogger.error(f"Error resetting guild {guild_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to reset guild") from e


@router.delete("/guilds/{guild_id}/uninstall")
async def uninstall_bot(guild_id: int, user_id: int, config_service: ConfigService = Depends(get_config_service)):
    """
    Completely remove all bot data for a guild.

    WARNING: This is irreversible and removes all player data, configurations, and shops.
    Requires admin permissions (user_id must be in ADMIN_USER_IDS).
    """
    flogger.warning(f"Uninstalling bot from guild {guild_id} - ALL DATA WILL BE LOST")

    if not await verify_admin_permissions(guild_id, user_id):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    try:
        async with get_db_session() as db:
            # Remove all guild data
            removed_counts = await config_service.uninstall_guild(db, guild_id)

            flogger.warning(f"Uninstalled bot from guild {guild_id}: {removed_counts}")

            await AuditService.log_action(
                db,
                user_id=user_id,
                action="guild_uninstall",
                guild_id=guild_id,
                resource_type="guild",
                resource_id=str(guild_id),
                details={"removed_counts": removed_counts},
            )

            return {
                "guild_id": guild_id,
                "removed_counts": removed_counts,
                "message": f"Bot completely uninstalled from guild {guild_id}",
                "warning": "All data has been permanently deleted",
            }

    except Exception as e:
        flogger.error(f"Error uninstalling bot from guild {guild_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to uninstall bot") from e


@router.put("/players/credits")
async def update_player_credits(
    request: UpdatePlayerCreditsRequest,
    user_id: int,
    guild_id: int,
    player_service: PlayerService = Depends(get_player_service),
):
    """Update a player's credits. Requires admin permissions."""
    if not await verify_admin_permissions(guild_id, user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")

    flogger.info(f"Admin updating credits for player {request.player_id}: {request.credits}")

    try:
        async with get_db_session() as db:
            player = await player_service.update_player_credits(
                db, request.player_id, request.credits, request.update_lifetime
            )

            await AuditService.log_action(
                db,
                user_id=user_id,
                action="credits_update",
                guild_id=guild_id,
                resource_type="player",
                resource_id=str(request.player_id),
                details={"player_id": request.player_id, "credits": request.credits},
            )

            return {
                "player_id": request.player_id,
                "old_credits": player.credits - request.credits,
                "new_credits": request.credits,
                "lifetime_credits": player.lifetime_credits,
                "message": f"Credits updated for player {request.player_id}",
            }

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error updating credits for player {request.player_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update credits") from e


@router.put("/players/xp")
async def update_player_xp(
    request: UpdatePlayerXPRequest,
    user_id: int,
    guild_id: int,
    player_service: PlayerService = Depends(get_player_service),
):
    """Update a player's XP and check for tier advancement. Requires admin permissions."""
    if not await verify_admin_permissions(guild_id, user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")

    flogger.info(f"Admin updating XP for player {request.player_id}: {request.xp}")

    try:
        async with get_db_session() as db:
            old_player = await player_service.player_repo.get_by_id(db, request.player_id)
            if not old_player:
                raise HTTPException(status_code=404, detail="Player not found")

            old_tier = old_player.tier
            player = await player_service.update_player_xp(db, request.player_id, request.xp)

            await AuditService.log_action(
                db,
                user_id=user_id,
                action="xp_update",
                guild_id=guild_id,
                resource_type="player",
                resource_id=str(request.player_id),
                details={"player_id": request.player_id, "xp": request.xp},
            )

            return {
                "player_id": request.player_id,
                "old_xp": old_player.xp,
                "new_xp": request.xp,
                "old_tier": old_tier,
                "new_tier": player.tier,
                "tier_changed": old_tier != player.tier,
                "message": f"XP updated for player {request.player_id}",
            }

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error updating XP for player {request.player_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update XP") from e


@router.post("/players/{player_id}/reset")
async def reset_player(
    player_id: int, user_id: int, guild_id: int, player_service: PlayerService = Depends(get_player_service)
):
    """Reset a player's stats back to defaults. Requires admin permissions."""
    if not await verify_admin_permissions(guild_id, user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")

    flogger.info(f"Admin resetting player {player_id} stats to defaults")

    try:
        async with get_db_session() as db:
            player = await player_service.player_repo.get_by_id(db, player_id)
            if not player:
                raise HTTPException(status_code=404, detail="Player not found")

            # Get guild config for starting credits
            config = await player_service.config_repo.get_by_guild_id(db, player.guild_id)
            starting_credits = config.starting_credits if config else 0

            # Reset stats to defaults
            player.credits = starting_credits
            player.xp = 0
            player.tier = "Bronze"
            player.bounty_wins = 0
            player.duel_wins = 0
            player.duel_losses = 0
            player.prestige_count = 0

            await db.commit()
            await db.refresh(player)

            flogger.info(f"Reset player {player_id} stats to defaults")

            await AuditService.log_action(
                db,
                user_id=user_id,
                action="player_reset",
                guild_id=guild_id,
                resource_type="player",
                resource_id=str(player_id),
            )

            return {
                "player_id": player_id,
                "credits": player.credits,
                "xp": player.xp,
                "tier": player.tier,
                "bounty_wins": player.bounty_wins,
                "duel_wins": player.duel_wins,
                "duel_losses": player.duel_losses,
                "prestige_count": player.prestige_count,
                "message": f"Player {player_id} stats reset to defaults",
            }

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error resetting player {player_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to reset player") from e


@router.post("/players/inventory/add")
async def add_inventory_item(
    request: AddInventoryItemRequest,
    user_id: int,
    guild_id: int,
    inventory_service: InventoryService = Depends(get_inventory_service),
):
    """Add items to a player's inventory. Requires admin permissions."""
    if not await verify_admin_permissions(guild_id, user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")

    flogger.info(f"Admin adding {request.quantity}x {request.item_name} to player {request.player_id}")

    try:
        async with get_db_session() as db:
            transaction_details = await inventory_service.add_item_to_inventory(
                db,
                request.player_id,
                request.item_type,
                request.item_name,
                request.quantity,
            )

            await AuditService.log_action(
                db,
                user_id=user_id,
                action="inventory_add",
                guild_id=guild_id,
                resource_type="inventory",
                resource_id=str(request.player_id),
                details={
                    "player_id": request.player_id,
                    "item_name": request.item_name,
                    "quantity": request.quantity,
                },
            )

            return {
                **transaction_details,
                "message": (f"Added {request.quantity}x {request.item_name} to player {request.player_id} inventory"),
            }

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error adding inventory item: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add inventory item"
        ) from e


@router.post("/shops/refresh")
async def refresh_shop(
    request: RefreshShopRequest, user_id: int, shop_service: ShopService = Depends(get_shop_service)
):
    """Force refresh a shop's inventory. Requires admin permissions."""
    if not await verify_admin_permissions(request.guild_id, user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")

    flogger.info(f"Admin refreshing {request.tier} shop for guild {request.guild_id}")

    try:
        async with get_db_session() as db:
            refresh_details = await shop_service.refresh_shop(
                db, request.guild_id, request.tier, request.force_tech_level
            )

            await AuditService.log_action(
                db,
                user_id=user_id,
                action="shop_refresh",
                guild_id=request.guild_id,
                resource_type="shop",
                resource_id=str(request.guild_id),
                details={"tier": request.tier},
            )

            return {
                **refresh_details,
                "message": f"Successfully refreshed {request.tier} shop for guild {request.guild_id}",
            }

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error refreshing shop: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to refresh shop") from e


@router.put("/shops/config")
async def update_shop_config(
    request: UpdateShopConfigRequest, user_id: int, config_service: ConfigService = Depends(get_config_service)
):
    """Update shop configuration parameters. Requires admin permissions."""
    if not await verify_admin_permissions(request.guild_id, user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")

    flogger.info(f"Admin updating shop config for guild {request.guild_id}")

    try:
        async with get_db_session() as db:
            config = await config_service.update_shop_config(db, request.model_dump(exclude_unset=True))

            await AuditService.log_action(
                db,
                user_id=user_id,
                action="shop_config_update",
                guild_id=request.guild_id,
                resource_type="shop",
                resource_id=str(request.guild_id),
                details=request.model_dump(exclude_unset=True),
            )

            return {
                "guild_id": request.guild_id,
                "updated_config": config,
                "message": f"Shop configuration updated for guild {request.guild_id}",
            }

    except Exception as e:
        flogger.error(f"Error updating shop config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update shop configuration"
        ) from e


@router.get("/system/health", response_model=SystemHealthResponse)
async def get_system_health(
    user_id: int,
    player_service: PlayerService = Depends(get_player_service),
    shop_service: ShopService = Depends(get_shop_service),
):
    """Get comprehensive system health information. Requires admin permissions."""
    # Use guild_id=0 as sentinel for system-level checks (no specific guild context)
    if not await verify_admin_permissions(0, user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")

    flogger.debug("Admin requesting system health information")

    try:
        async with get_db_session() as db:
            total_users = await player_service.user_repo.count(db)
            total_players = await player_service.player_repo.count(db)
            total_guilds = await player_service.config_repo.count(db)
            shop_items_count = await shop_service.shop_repo.count(db)

            health_info = {
                "database_status": "healthy",
                "total_users": total_users,
                "total_players": total_players,
                "total_guilds": total_guilds,
                "shop_items_count": shop_items_count,
                "system_status": "operational",
            }

            return SystemHealthResponse(**health_info)

    except Exception as e:
        flogger.error(f"Error getting system health: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get system health"
        ) from e


@router.get("/guilds/{guild_id}/stats")
async def get_guild_statistics(
    guild_id: int, user_id: int, player_service: PlayerService = Depends(get_player_service)
):
    """Get comprehensive statistics for a guild. Requires admin permissions."""
    if not await verify_admin_permissions(guild_id, user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")

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
                "average_xp": total_xp / total_players if total_players > 0 else 0,
            }

            return stats

    except Exception as e:
        flogger.error(f"Error getting guild statistics: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get guild statistics"
        ) from e
