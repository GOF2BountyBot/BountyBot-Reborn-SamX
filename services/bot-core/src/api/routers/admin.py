"""
Admin API router for the BountyBot inventory system.

Handles administrative operations including:
- Guild initialization and configuration
- Player management (credits, XP, inventory)
- Shop management and refresh
- Role-based access control
- System health and statistics
"""

import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request, status
from persist.database.manager import get_db_session
from persist.models.player_ship import PlayerShip
from persist.repositories.player_repository import PlayerRepository
from persist.repositories.player_ship_repository import PlayerShipRepository
from persist.repositories.ship_repository import ShipRepository
from services.audit_service import AuditService
from services.bounty_service import BountyService
from services.config_service import ConfigService
from services.inventory_service import InventoryService
from services.player_service import PlayerService
from services.shop_service import ShopService
from shared import bblogger

from api.schemas.admin_schema import (
    AddInventoryItemRequest,
    AdminGiveItemRequest,
    AdminGiveShipRequest,
    AdminRemoveItemRequest,
    AdminRemoveShipRequest,
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
                "category_id": request.category_id,
                "shop_channel_id": request.shop_channel_id,
                "bronze_bounty_channel_id": request.bronze_bounty_channel_id,
                "silver_bounty_channel_id": request.silver_bounty_channel_id,
                "gold_bounty_channel_id": request.gold_bounty_channel_id,
                "hunting_channel_id": request.hunting_channel_id,
                "discussion_channel_id": request.discussion_channel_id,
                "image_channel_id": request.image_channel_id,
                "bounty_hunter_role_id": request.bounty_hunter_role_id,
                "bronze_role_id": request.bronze_role_id,
                "silver_role_id": request.silver_role_id,
                "gold_role_id": request.gold_role_id,
                "platinum_bounty_channel_id": request.platinum_bounty_channel_id,
                "platinum_role_id": request.platinum_role_id,
            }

            await config_service.create_or_update_config(db, config_data)

            # Initialize empty shops for all tiers
            shops_created = 0
            tiers = ["Bronze", "Silver", "Gold", "Platinum"]

            for tier in tiers:
                await shop_service.refresh_shop(db, request.guild_id, tier)
                shops_created += 1

            flogger.info(f"Successfully initialized guild {request.guild_id}")

            channels_configured = any(
                [
                    request.category_id,
                    request.shop_channel_id,
                    request.bronze_bounty_channel_id,
                    request.silver_bounty_channel_id,
                    request.gold_bounty_channel_id,
                    request.hunting_channel_id,
                    request.discussion_channel_id,
                    request.image_channel_id,
                ]
            )

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
                channels_configured=channels_configured,
                bounty_hunter_role_id=request.bounty_hunter_role_id,
                bronze_role_id=request.bronze_role_id,
                silver_role_id=request.silver_role_id,
                gold_role_id=request.gold_role_id,
                platinum_role_id=request.platinum_role_id,
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
    request: Request,
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

    # Best-effort: cancel scheduled jobs for this guild before resetting
    jobs_cancelled = 0
    try:
        scheduler = getattr(request.app.state, "scheduler", None)
        if scheduler is not None:
            for job in scheduler.get_jobs():
                try:
                    job_args = list(job.args) if job.args else []
                    # args[1] is the payload dict for run_job(job_id, payload)
                    payload = job_args[1] if len(job_args) > 1 else {}
                    payload_str = json.dumps(payload, default=str)
                    if str(guild_id) in payload_str:
                        scheduler.remove_job(job.id)
                        jobs_cancelled += 1
                        flogger.info(f"Cancelled job {job.id} for guild {guild_id} during reset")
                except Exception as _job_exc:
                    flogger.warning(f"Non-fatal: failed to inspect/cancel job {getattr(job, 'id', '?')}: {_job_exc}")
        else:
            flogger.debug("Scheduler not available during reset; skipping job cancellation")
    except Exception as sched_exc:
        flogger.warning(f"Non-fatal: error during scheduler cleanup for guild {guild_id}: {sched_exc}")

    # Best-effort: clear active bounties for the guild
    bounties_cleared = 0
    try:
        bounty_service = BountyService()
        async with get_db_session() as db:
            result = await bounty_service.clear_bounties(db, guild_id, tier=None)
            bounties_cleared = result.get("cleared_count", 0)
            flogger.info(f"Cleared {bounties_cleared} bounties for guild {guild_id} during reset")
    except Exception as bounty_exc:
        flogger.warning(f"Non-fatal: failed to clear bounties for guild {guild_id} during reset: {bounty_exc}")

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
                details={"preserve_players": preserve_players, "jobs_cancelled": jobs_cancelled},
            )

            return {
                "guild_id": guild_id,
                "players_preserved": preserve_players,
                "shops_refreshed": shops_refreshed,
                "bounties_cleared": bounties_cleared,
                "jobs_cancelled": jobs_cancelled,
                "message": f"Guild {guild_id} reset successfully",
            }

    except Exception as e:
        flogger.error(f"Error resetting guild {guild_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to reset guild") from e


@router.delete("/guilds/{guild_id}/uninstall")
async def uninstall_bot(
    guild_id: int,
    user_id: int,
    request: Request,
    config_service: ConfigService = Depends(get_config_service),
):
    """
    Completely remove all bot data for a guild.

    WARNING: This is irreversible and removes all player data, configurations, and shops.
    Requires admin permissions (user_id must be in ADMIN_USER_IDS).
    """
    flogger.warning(f"Uninstalling bot from guild {guild_id} - ALL DATA WILL BE LOST")

    if not await verify_admin_permissions(guild_id, user_id):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Best-effort: cancel scheduled jobs for this guild before removing DB data
    jobs_cancelled = 0
    try:
        scheduler = getattr(request.app.state, "scheduler", None)
        if scheduler is not None:
            for job in scheduler.get_jobs():
                # Check if job args contain the guild_id
                try:
                    job_args = list(job.args) if job.args else []
                    # args[1] is the payload dict for run_job(job_id, payload)
                    payload = job_args[1] if len(job_args) > 1 else {}
                    payload_str = json.dumps(payload, default=str)
                    if str(guild_id) in payload_str:
                        scheduler.remove_job(job.id)
                        jobs_cancelled += 1
                        flogger.info(f"Cancelled job {job.id} for guild {guild_id} during uninstall")
                except Exception as _job_exc:
                    flogger.warning(f"Non-fatal: failed to inspect/cancel job {getattr(job, 'id', '?')}: {_job_exc}")
        else:
            flogger.debug("Scheduler not available during uninstall; skipping job cancellation")
    except Exception as sched_exc:
        flogger.warning(f"Non-fatal: error during scheduler cleanup for guild {guild_id}: {sched_exc}")

    # Best-effort: clear active bounties for the guild
    bounties_cleared = 0
    try:
        bounty_service = BountyService()
        async with get_db_session() as db:
            result = await bounty_service.clear_bounties(db, guild_id, tier=None)
            bounties_cleared = result.get("cleared_count", 0)
            flogger.info(f"Cleared {bounties_cleared} bounties for guild {guild_id} during uninstall")
    except Exception as bounty_exc:
        flogger.warning(f"Non-fatal: failed to clear bounties for guild {guild_id} during uninstall: {bounty_exc}")

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
                details={"removed_counts": removed_counts, "jobs_cancelled": jobs_cancelled},
            )

            return {
                "guild_id": guild_id,
                "removed_counts": removed_counts,
                "jobs_cancelled": jobs_cancelled,
                "bounties_cleared": bounties_cleared,
                "message": f"Bot completely uninstalled from guild {guild_id}",
                "warning": "All data has been permanently deleted",
            }

    except Exception as e:
        flogger.error(f"Error uninstalling bot from guild {guild_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to uninstall bot") from e


@router.delete("/guilds/{guild_id}/cleanup")
async def cleanup_guild_on_remove(
    guild_id: int,
    request: Request,
    config_service: ConfigService = Depends(get_config_service),
):
    """
    Soft cleanup invoked by the discord-gateway ``on_guild_remove`` event.

    Removes guild-scoped DB state (``guild_configs``, ``guild_shops``, ``bounty``,
    ``apscheduler_jobs``, players & cascaded ships/inventory) for a guild the bot
    has just left. Does NOT touch Discord channels/roles — the bot is already
    gone from the guild, so Discord side cannot be modified.

    This endpoint is intentionally distinct from ``DELETE /uninstall``:

    - ``/uninstall`` is a destructive admin command behind ADMIN_USER_IDS auth
      and intends to delete Discord-side artifacts as well.
    - ``/cleanup`` is a system-event endpoint with NO user_id parameter —
      the actor is the Discord platform event itself (recorded as
      ``actor_id=0`` in the audit log with explanatory ``details``).

    **Idempotency**: the endpoint always returns 200 even when no guild data
    exists to remove. Discord may retry guild-remove events, and a second
    invocation must succeed silently rather than 404.
    """
    flogger.info(f"Cleanup invoked for guild {guild_id} (on_guild_remove event)")

    # Best-effort: cancel scheduled jobs for this guild before removing DB data.
    # Mirrors the uninstall pattern but without admin auth (system event).
    jobs_cancelled = 0
    try:
        scheduler = getattr(request.app.state, "scheduler", None)
        if scheduler is not None:
            for job in scheduler.get_jobs():
                try:
                    job_args = list(job.args) if job.args else []
                    payload = job_args[1] if len(job_args) > 1 else {}
                    payload_str = json.dumps(payload, default=str)
                    if str(guild_id) in payload_str:
                        scheduler.remove_job(job.id)
                        jobs_cancelled += 1
                        flogger.info(f"Cancelled job {job.id} for guild {guild_id} during cleanup")
                except Exception as _job_exc:
                    flogger.warning(f"Non-fatal: failed to inspect/cancel job {getattr(job, 'id', '?')}: {_job_exc}")
        else:
            flogger.debug("Scheduler not available during cleanup; skipping job cancellation")
    except Exception as sched_exc:
        flogger.warning(f"Non-fatal: error during scheduler cleanup for guild {guild_id}: {sched_exc}")

    # Best-effort: clear active bounties for the guild.
    bounties_cleared = 0
    try:
        bounty_service = BountyService()
        async with get_db_session() as db:
            result = await bounty_service.clear_bounties(db, guild_id, tier=None)
            bounties_cleared = result.get("cleared_count", 0)
            flogger.info(f"Cleared {bounties_cleared} bounties for guild {guild_id} during cleanup")
    except Exception as bounty_exc:
        flogger.warning(f"Non-fatal: failed to clear bounties for guild {guild_id} during cleanup: {bounty_exc}")

    try:
        async with get_db_session() as db:
            # Remove all guild DB data — same code path as /uninstall.
            # Idempotency: clear_guild_players iterates a (possibly empty) list,
            # clear_all_guild_shops issues a DELETE WHERE that's a no-op on empty,
            # and delete_guild_config returns False if no row exists.
            removed_counts = await config_service.uninstall_guild(db, guild_id)

            flogger.info(f"Cleanup of guild {guild_id} complete: {removed_counts}")

            # Audit-log the system event. actor_id=0 is the convention for
            # "no user — automated platform event"; details documents the trigger.
            await AuditService.log_action(
                db,
                user_id=0,
                action="guild_cleanup_on_remove",
                guild_id=guild_id,
                resource_type="guild",
                resource_id=str(guild_id),
                details={
                    "removed_counts": removed_counts,
                    "jobs_cancelled": jobs_cancelled,
                    "bounties_cleared": bounties_cleared,
                    "trigger": "on_guild_remove event from discord-gateway",
                },
            )

            return {
                "guild_id": guild_id,
                "removed_counts": removed_counts,
                "jobs_cancelled": jobs_cancelled,
                "bounties_cleared": bounties_cleared,
                "message": f"Soft cleanup of guild {guild_id} complete (DB-only, Discord-side untouched)",
            }

    except Exception as e:
        flogger.error(f"Error during cleanup of guild {guild_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to cleanup guild") from e


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
            # Pre-capture old_credits BEFORE the service mutates the player in-place
            # (identity-map sequencing: after update_player_credits(), player.credits
            # already holds the new value — reading it post-call yields 0 for old_credits)
            old_player = await player_service.player_repo.get_by_id(db, request.player_id)
            if not old_player:
                raise ValueError(f"Player {request.player_id} not found")
            old_credits = old_player.credits

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
                "old_credits": old_credits,
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
            # Pre-capture old_xp BEFORE the service mutates the player in-place
            # (identity-map sequencing: after update_player_xp(), old_player.xp
            # already holds the new value — reading it post-call yields wrong old_xp)
            old_xp = old_player.xp
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
                "old_xp": old_xp,
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
    """Force refresh a shop's inventory and announce the restock. Requires admin permissions.

    After a successful DB refresh the endpoint posts a shop-refresh announcement
    to the guild's ``#shop`` channel (looked up from ``GuildConfig.shop_channel_id``).
    The announcement mirrors the one posted by the scheduled ``shop_refresh_executor``.

    Announcement failures are **non-fatal** — the refresh succeeds even when the
    discord-gateway call fails, and the response includes an ``announcement_warning``
    field in that case.
    """
    if not await verify_admin_permissions(request.guild_id, user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")

    flogger.info(f"Admin refreshing {request.tier} shop for guild={request.guild_id} user={user_id}")

    try:
        shop_channel_id: int | None = None
        bounty_hunter_role_id: int | None = None

        async with get_db_session() as db:
            refresh_details = await shop_service.refresh_shop(
                db, request.guild_id, request.tier, request.force_tech_level
            )

            # Look up the guild config so we can announce to the right channel.
            # The lookup is best-effort: if it fails (e.g. in tests with simple
            # AsyncMock sessions) we proceed without a channel ID and skip the
            # announcement rather than failing the refresh.
            try:
                from persist.repositories.config_repository import ConfigRepository

                config_repo = ConfigRepository()
                config = await config_repo.get_by_guild_id(db, request.guild_id)
                shop_channel_id = getattr(config, "shop_channel_id", None) if config else None
                bounty_hunter_role_id = getattr(config, "bounty_hunter_role_id", None) if config else None
            except Exception as cfg_exc:  # pylint: disable=broad-exception-caught
                flogger.warning(
                    f"Admin shop refresh: could not look up guild config for guild={request.guild_id}, "
                    f"announcement will be skipped: {cfg_exc}"
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

        # Announce AFTER the DB session is closed so the commit is visible to readers.
        # This call is non-fatal — errors are logged inside announce_shop_refresh.
        from utils.shop_announcement import announce_shop_refresh

        announcement_warning: str | None = None
        try:
            await announce_shop_refresh(
                caller_label=f"AdminRefresh[guild={request.guild_id}]",
                guild_id=request.guild_id,
                channel_id=shop_channel_id,
                bounty_hunter_role_id=bounty_hunter_role_id,
                tier=request.tier,
            )
        except Exception as ann_exc:  # pylint: disable=broad-exception-caught
            # Should not reach here (announce_shop_refresh swallows its own errors),
            # but guard defensively so the admin response is always returned.
            flogger.error(f"Admin shop refresh announcement failed for guild={request.guild_id}: {ann_exc}")
            announcement_warning = "Announcement to #shop failed — shop was still refreshed successfully."

        response: dict = {
            **refresh_details,
            "message": f"Successfully refreshed {request.tier} shop for guild {request.guild_id}",
        }
        if announcement_warning:
            response["announcement_warning"] = announcement_warning
        return response

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error refreshing shop for guild={request.guild_id}: {e}")
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


@router.post("/give-item")
async def admin_give_item(
    request: AdminGiveItemRequest,
    admin_user_id: int,
    inventory_service: InventoryService = Depends(get_inventory_service),
):
    """Give an item directly to a player's inventory (no credit cost). Requires admin permissions."""
    if not await verify_admin_permissions(request.guild_id, admin_user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")

    flogger.info(
        f"Admin giving {request.quantity}x {request.item_name} ({request.item_type}) "
        f"to user {request.user_id} in guild {request.guild_id}"
    )

    try:
        async with get_db_session() as db:
            # Resolve Discord user_id → player_id
            player_repo = PlayerRepository()
            from persist.repositories.user_repository import UserRepository

            user_repo = UserRepository()
            user = await user_repo.get_by_discord_id(db, request.user_id)
            if not user:
                raise HTTPException(status_code=404, detail=f"User {request.user_id} not found")

            player = await player_repo.get_by_user_and_guild(db, user.id, request.guild_id)
            if not player:
                raise HTTPException(
                    status_code=404, detail=f"Player not found for user {request.user_id} in guild {request.guild_id}"
                )

            transaction_details = await inventory_service.add_item_to_inventory(
                db,
                player.id,
                request.item_type,
                request.item_name,
                request.quantity,
            )

            await AuditService.log_action(
                db,
                user_id=admin_user_id,
                action="admin_give_item",
                guild_id=request.guild_id,
                resource_type="inventory",
                resource_id=str(player.id),
                details={
                    "player_id": player.id,
                    "item_name": request.item_name,
                    "item_type": request.item_type,
                    "quantity": request.quantity,
                },
            )

            return {
                **transaction_details,
                "message": (
                    f"Gave {request.quantity}x {request.item_name} to player {player.id} in guild {request.guild_id}"
                ),
            }

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error giving item to player: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to give item") from e


@router.post("/remove-item")
async def admin_remove_item(
    request: AdminRemoveItemRequest,
    admin_user_id: int,
    inventory_service: InventoryService = Depends(get_inventory_service),
):
    """Remove an item from a player's inventory. Requires admin permissions."""
    if not await verify_admin_permissions(request.guild_id, admin_user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")

    flogger.info(
        f"Admin removing {request.quantity}x {request.item_name} ({request.item_type}) "
        f"from user {request.user_id} in guild {request.guild_id}"
    )

    try:
        async with get_db_session() as db:
            # Resolve Discord user_id → player_id
            player_repo = PlayerRepository()
            from persist.repositories.user_repository import UserRepository

            user_repo = UserRepository()
            user = await user_repo.get_by_discord_id(db, request.user_id)
            if not user:
                raise HTTPException(status_code=404, detail=f"User {request.user_id} not found")

            player = await player_repo.get_by_user_and_guild(db, user.id, request.guild_id)
            if not player:
                raise HTTPException(
                    status_code=404, detail=f"Player not found for user {request.user_id} in guild {request.guild_id}"
                )

            transaction_details = await inventory_service.remove_item_from_inventory(
                db,
                player.id,
                request.item_type,
                request.item_name,
                request.quantity,
            )

            await AuditService.log_action(
                db,
                user_id=admin_user_id,
                action="admin_remove_item",
                guild_id=request.guild_id,
                resource_type="inventory",
                resource_id=str(player.id),
                details={
                    "player_id": player.id,
                    "item_name": request.item_name,
                    "item_type": request.item_type,
                    "quantity": request.quantity,
                },
            )

            return {
                **transaction_details,
                "message": (
                    f"Removed {request.quantity}x {request.item_name} "
                    f"from player {player.id} in guild {request.guild_id}"
                ),
            }

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error removing item from player: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to remove item") from e


@router.post("/give-ship")
async def admin_give_ship(
    request: AdminGiveShipRequest,
    admin_user_id: int,
):
    """Give a ship to a player (starts inactive with empty loadout). Requires admin permissions."""
    if not await verify_admin_permissions(request.guild_id, admin_user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")

    flogger.info(f"Admin giving ship {request.ship_name} to user {request.user_id} in guild {request.guild_id}")

    try:
        async with get_db_session() as db:
            # Validate the ship exists in game data
            ship_repo = ShipRepository()
            game_ship = await ship_repo.get_by_name(db, request.ship_name)
            if not game_ship:
                raise HTTPException(status_code=404, detail=f"Ship '{request.ship_name}' does not exist in game data")

            # Resolve Discord user_id → player_id
            player_repo = PlayerRepository()
            from persist.repositories.user_repository import UserRepository

            user_repo = UserRepository()
            user = await user_repo.get_by_discord_id(db, request.user_id)
            if not user:
                raise HTTPException(status_code=404, detail=f"User {request.user_id} not found")

            player = await player_repo.get_by_user_and_guild(db, user.id, request.guild_id)
            if not player:
                raise HTTPException(
                    status_code=404, detail=f"Player not found for user {request.user_id} in guild {request.guild_id}"
                )

            # Create the PlayerShip record (inactive, empty loadout)
            player_ship_repo = PlayerShipRepository()
            new_ship = PlayerShip(
                player_id=player.id,
                ship_name=request.ship_name,
                is_active=False,
                weapons=[],
                modules=[],
                turrets=[],
            )
            created_ship = await player_ship_repo.add(db, new_ship)

            await AuditService.log_action(
                db,
                user_id=admin_user_id,
                action="admin_give_ship",
                guild_id=request.guild_id,
                resource_type="player_ship",
                resource_id=str(created_ship.id),
                details={
                    "player_id": player.id,
                    "ship_name": request.ship_name,
                    "ship_id": created_ship.id,
                },
            )

            return {
                "player_id": player.id,
                "ship_id": created_ship.id,
                "ship_name": created_ship.ship_name,
                "is_active": created_ship.is_active,
                "message": f"Gave ship '{request.ship_name}' to player {player.id} in guild {request.guild_id}",
            }

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error giving ship to player: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to give ship") from e


@router.post("/remove-ship")
async def admin_remove_ship(
    request: AdminRemoveShipRequest,
    admin_user_id: int,
):
    """Remove a ship from a player. Unequips all items first. Cannot remove only active ship.
    Requires admin permissions.
    """
    if not await verify_admin_permissions(request.guild_id, admin_user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")

    flogger.info(f"Admin removing ship {request.ship_name} from user {request.user_id} in guild {request.guild_id}")

    try:
        # Package G (B.19): wrap in db.begin() (atomicity gap fix) and route
        # evacuation through the LoadoutConsistencyService anti-duplication
        # guard (closes the phantom-item materialisation exploit).
        async with get_db_session() as db, db.begin():
            # Resolve Discord user_id → player_id
            player_repo = PlayerRepository()
            from persist.repositories.user_repository import UserRepository
            from services.loadout_consistency_service import LoadoutConsistencyService

            user_repo = UserRepository()
            user = await user_repo.get_by_discord_id(db, request.user_id)
            if not user:
                raise HTTPException(status_code=404, detail=f"User {request.user_id} not found")

            player = await player_repo.get_by_user_and_guild(db, user.id, request.guild_id)
            if not player:
                raise HTTPException(
                    status_code=404, detail=f"Player not found for user {request.user_id} in guild {request.guild_id}"
                )

            # Find the ship by name owned by this player
            player_ship_repo = PlayerShipRepository()
            ships = await player_ship_repo.get_ships_by_name(db, player.id, request.ship_name)
            if not ships:
                raise HTTPException(
                    status_code=404,
                    detail=f"Player does not own a ship named '{request.ship_name}'",
                )

            # Target the first matching ship
            ship = ships[0]

            # Check if this is the active ship — only block if it's the ONLY ship
            all_ships = await player_ship_repo.get_player_ships(db, player.id)
            if ship.is_active and len(all_ships) == 1:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot remove the player's only active ship",
                )

            # Evacuate equipped items via the consistency service (anti-duplication guard).
            consistency = LoadoutConsistencyService()
            evac = await consistency.evacuate_ship_loadout_to_inventory(db, ship=ship)
            items_returned: list[str] = list(evac["items_returned"])

            # Delete the ship
            await player_ship_repo.remove(db, ship, commit=False)

            # Inside the wrapping `async with db.begin():` block — the audit
            # row joins the primary transaction so it persists atomically with
            # the ship removal (or rolls back together if any step fails).
            await AuditService.log_action(
                db,
                user_id=admin_user_id,
                action="admin_remove_ship",
                guild_id=request.guild_id,
                resource_type="player_ship",
                resource_id=str(ship.id),
                details={
                    "player_id": player.id,
                    "ship_name": request.ship_name,
                    "ship_id": ship.id,
                    "items_returned": items_returned,
                },
                commit=False,
            )

            return {
                "player_id": player.id,
                "ship_id": ship.id,
                "ship_name": ship.ship_name,
                "items_returned_to_inventory": items_returned,
                "message": (
                    f"Removed ship '{request.ship_name}' from player {player.id} in guild {request.guild_id}. "
                    f"{len(items_returned)} item(s) returned to inventory."
                ),
            }

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error removing ship from player: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to remove ship") from e
