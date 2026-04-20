"""
Players API router for the BountyBot inventory system.

Handles REST API endpoints for player management, progression, and statistics.
This router follows the requirement that all major subsystem interactions
must be done via REST API.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from persist.database.manager import get_db_session
from services.exceptions import GuildNotConfiguredError
from services.player_service import PlayerService
from shared import bblogger
from sqlalchemy.exc import IntegrityError

from api.schemas.players_schema import (
    CargoItem,
    CreatePlayerRequest,
    LoadoutModuleItem,
    LoadoutWeaponItem,
    PlayerLoadoutResponse,
    PlayerResponse,
    PlayerStatisticsResponse,
    PrestigeResponse,
    PromoteResponse,
    PromotionStatusResponse,
    TransferCreditsRequest,
    TransferCreditsResponse,
    UpdateCreditsRequest,
    UpdateXPRequest,
)

flogger = bblogger.get_logger("players-api-router")

router = APIRouter(
    prefix="/players",
    tags=["players"],
    responses={404: {"description": "Player not found"}, 500: {"description": "Internal server error"}},
)


# Dependency injection
async def get_player_service():
    return PlayerService()


@router.post("/", response_model=PlayerResponse, status_code=status.HTTP_201_CREATED)
async def create_or_get_player(
    request: CreatePlayerRequest, player_service: PlayerService = Depends(get_player_service)
):
    """
    Create a new player or get existing one for a Discord user in a guild.

    This is the main endpoint called when a user first interacts with the bot
    in a specific guild. Creates the player with starter loadout if needed.
    """
    flogger.info(f"Creating/getting player for Discord user {request.discord_id} in guild {request.guild_id}")

    try:
        async with get_db_session() as db:
            player = await player_service.get_or_create_player(
                db, request.discord_id, request.guild_id, request.discord_username
            )

            return PlayerResponse(
                id=player.id,
                user_id=player.user_id,
                guild_id=player.guild_id,
                credits=player.credits,
                lifetime_credits=player.lifetime_credits,
                systems_checked=player.systems_checked,
                bounty_wins=player.bounty_wins,
                xp=player.xp,
                tier=player.tier,
                prestige_count=player.prestige_count,
                duel_wins=player.duel_wins,
                duel_losses=player.duel_losses,
                duel_credits_won=player.duel_credits_won,
                duel_credits_lost=player.duel_credits_lost,
                active_ship_id=player.active_ship_id,
                created_at=player.created_at.isoformat(),
                updated_at=player.updated_at.isoformat(),
            )

    except GuildNotConfiguredError as e:
        flogger.warning(f"Guild not configured for player creation: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Guild not configured; admin must run /admin_setup",
        ) from e
    except ValueError as e:
        flogger.warning(f"Validation error creating/getting player: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except IntegrityError as e:
        flogger.error(f"Integrity error creating/getting player: {e}")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Player record conflict") from e
    except Exception as e:
        flogger.error(f"Error creating/getting player: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create or get player"
        ) from e


@router.get("/{player_id}", response_model=PlayerResponse)
async def get_player(player_id: int, player_service: PlayerService = Depends(get_player_service)):
    """Get a player by ID."""
    flogger.debug(f"Getting player: {player_id}")

    try:
        async with get_db_session() as db:
            player = await player_service.player_repo.get_by_id(db, player_id)
            if not player:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Player {player_id} not found")

            return PlayerResponse(
                id=player.id,
                user_id=player.user_id,
                guild_id=player.guild_id,
                credits=player.credits,
                lifetime_credits=player.lifetime_credits,
                systems_checked=player.systems_checked,
                bounty_wins=player.bounty_wins,
                xp=player.xp,
                tier=player.tier,
                prestige_count=player.prestige_count,
                duel_wins=player.duel_wins,
                duel_losses=player.duel_losses,
                duel_credits_won=player.duel_credits_won,
                duel_credits_lost=player.duel_credits_lost,
                active_ship_id=player.active_ship_id,
                created_at=player.created_at.isoformat(),
                updated_at=player.updated_at.isoformat(),
            )

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error getting player {player_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get player") from e


@router.get("/guild/{guild_id}", response_model=list[PlayerResponse])
async def get_players_by_guild(
    guild_id: int,
    skip: int = 0,
    limit: int = 100,
    tier: str | None = None,
    player_service: PlayerService = Depends(get_player_service),
):
    """Get all players in a guild, optionally filtered by tier."""
    flogger.debug(f"Getting players for guild {guild_id}, tier filter: {tier}")

    try:
        async with get_db_session() as db:
            if tier:
                players = await player_service.get_players_by_tier(db, guild_id, tier)
            else:
                players = await player_service.player_repo.get_players_by_guild(db, guild_id)

            # Apply pagination
            paginated_players = players[skip : skip + limit]

            return [
                PlayerResponse(
                    id=player.id,
                    user_id=player.user_id,
                    guild_id=player.guild_id,
                    credits=player.credits,
                    lifetime_credits=player.lifetime_credits,
                    systems_checked=player.systems_checked,
                    bounty_wins=player.bounty_wins,
                    xp=player.xp,
                    tier=player.tier,
                    prestige_count=player.prestige_count,
                    duel_wins=player.duel_wins,
                    duel_losses=player.duel_losses,
                    duel_credits_won=player.duel_credits_won,
                    duel_credits_lost=player.duel_credits_lost,
                    active_ship_id=player.active_ship_id,
                    created_at=player.created_at.isoformat(),
                    updated_at=player.updated_at.isoformat(),
                )
                for player in paginated_players
            ]

    except Exception as e:
        flogger.error(f"Error getting players for guild {guild_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get players") from e


@router.put("/{player_id}/credits", response_model=PlayerResponse)
async def update_player_credits(
    player_id: int, request: UpdateCreditsRequest, player_service: PlayerService = Depends(get_player_service)
):
    """Update player credits."""
    flogger.info(f"Updating credits for player {player_id}: {request.credits}")

    try:
        async with get_db_session() as db:
            player = await player_service.update_player_credits(db, player_id, request.credits, request.update_lifetime)

            return PlayerResponse(
                id=player.id,
                user_id=player.user_id,
                guild_id=player.guild_id,
                credits=player.credits,
                lifetime_credits=player.lifetime_credits,
                systems_checked=player.systems_checked,
                bounty_wins=player.bounty_wins,
                xp=player.xp,
                tier=player.tier,
                prestige_count=player.prestige_count,
                duel_wins=player.duel_wins,
                duel_losses=player.duel_losses,
                duel_credits_won=player.duel_credits_won,
                duel_credits_lost=player.duel_credits_lost,
                active_ship_id=player.active_ship_id,
                created_at=player.created_at.isoformat(),
                updated_at=player.updated_at.isoformat(),
            )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error updating credits for player {player_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update credits") from e


@router.put("/{player_id}/xp", response_model=PlayerResponse)
async def update_player_xp(
    player_id: int, request: UpdateXPRequest, player_service: PlayerService = Depends(get_player_service)
):
    """Update player XP and check for tier advancement."""
    flogger.info(f"Updating XP for player {player_id}: {request.xp}")

    try:
        async with get_db_session() as db:
            player = await player_service.update_player_xp(db, player_id, request.xp)

            return PlayerResponse(
                id=player.id,
                user_id=player.user_id,
                guild_id=player.guild_id,
                credits=player.credits,
                lifetime_credits=player.lifetime_credits,
                systems_checked=player.systems_checked,
                bounty_wins=player.bounty_wins,
                xp=player.xp,
                tier=player.tier,
                prestige_count=player.prestige_count,
                duel_wins=player.duel_wins,
                duel_losses=player.duel_losses,
                duel_credits_won=player.duel_credits_won,
                duel_credits_lost=player.duel_credits_lost,
                active_ship_id=player.active_ship_id,
                created_at=player.created_at.isoformat(),
                updated_at=player.updated_at.isoformat(),
            )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error updating XP for player {player_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update XP") from e


@router.post("/{player_id}/prestige", response_model=PrestigeResponse)
async def prestige_player(player_id: int, player_service: PlayerService = Depends(get_player_service)):
    """Prestige a player — reset progress, increment prestige counter.

    Player must be level 10 to prestige. Resets XP, xp_surplus, credits,
    tier, and inventory. Preserves lifetime_credits, ships, duel stats,
    and bounty stats.
    """
    flogger.info(f"Prestiging player {player_id}")

    try:
        async with get_db_session() as db:
            result = await player_service.prestige_player(db, player_id)
            return PrestigeResponse(**result)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error prestiging player {player_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to prestige player"
        ) from e


@router.get("/{player_id}/statistics", response_model=PlayerStatisticsResponse)
async def get_player_statistics(player_id: int, player_service: PlayerService = Depends(get_player_service)):
    """Get comprehensive player statistics."""
    flogger.debug(f"Getting statistics for player {player_id}")

    try:
        async with get_db_session() as db:
            stats = await player_service.get_player_statistics(db, player_id)
            return PlayerStatisticsResponse(**stats)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error getting statistics for player {player_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get player statistics"
        ) from e


@router.get("/{player_id}/promotion-status", response_model=PromotionStatusResponse)
async def get_promotion_status(player_id: int, player_service: PlayerService = Depends(get_player_service)):
    """Get promotion eligibility status for a player."""
    flogger.debug(f"Getting promotion status for player {player_id}")

    try:
        async with get_db_session() as db:
            result = await player_service.get_promotion_status(db, player_id)
            return PromotionStatusResponse(**result)

    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_msg) from e
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg) from e
    except Exception as e:
        flogger.error(f"Error getting promotion status for player {player_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get promotion status"
        ) from e


@router.put("/{player_id}/promote", response_model=PromoteResponse)
async def promote_player(player_id: int, player_service: PlayerService = Depends(get_player_service)):
    """Promote a player to the next tier if eligible."""
    flogger.info(f"Promoting player {player_id}")

    try:
        async with get_db_session() as db:
            result = await player_service.promote_player(db, player_id)
            return PromoteResponse(**result)

    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_msg) from e
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg) from e
    except Exception as e:
        flogger.error(f"Error promoting player {player_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to promote player") from e


@router.get("/{player_id}/loadout", response_model=PlayerLoadoutResponse)
async def get_player_loadout(
    player_id: int,
    include_cargo: bool = False,
    player_service: PlayerService = Depends(get_player_service),
):
    """Get the active ship loadout for a player, including computed HP and DPS stats."""
    flogger.debug(f"Getting loadout for player {player_id}, include_cargo={include_cargo}")

    try:
        async with get_db_session() as db:
            from persist.models.module import Module
            from persist.models.player_ship import PlayerShip
            from persist.models.ship import Ship
            from persist.repositories.item_repository import ItemRepository
            from sqlalchemy import select

            # 1. Get the player
            player = await player_service.player_repo.get_by_id(db, player_id)
            if not player:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Player {player_id} not found")

            # 2. Check for active ship
            if not player.active_ship_id:
                return PlayerLoadoutResponse(player_id=player.id, ship_name=None, message="No active ship")

            # 3. Load PlayerShip explicitly by ID (avoid lazy-loading active_ship relationship)
            result = await db.execute(select(PlayerShip).where(PlayerShip.id == player.active_ship_id))
            player_ship = result.scalars().first()
            if not player_ship:
                return PlayerLoadoutResponse(player_id=player.id, ship_name=None, message="No active ship")

            ship_name = player_ship.ship_name

            # 4. Get static Ship data (base armour, emoji, slot counts)
            ship_result = await db.execute(select(Ship).where(Ship.name == ship_name))
            ship = ship_result.scalars().first()
            base_armour = ship.armour if ship else 0
            ship_emoji = ship.emoji if ship else None

            # 5. Look up each equipped item by name
            item_repo = ItemRepository()

            equipped_weapons = player_ship.weapons or []
            equipped_modules = player_ship.modules or []
            equipped_turrets = player_ship.turrets or []

            # Build weapon items
            weapon_items: list[LoadoutWeaponItem] = []
            total_dps = 0.0
            for w_name in equipped_weapons:
                item = await item_repo.get_by_name(db, w_name, item_type="primary_weapon")
                if item is None:
                    item = await item_repo.get_by_name(db, w_name)
                dps = getattr(item, "dps", None) if item else None
                if dps:
                    total_dps += dps
                weapon_items.append(
                    LoadoutWeaponItem(
                        name=w_name,
                        emoji=item.emoji if item else None,
                        dps=dps,
                        value=item.value if item else None,
                    )
                )

            # Build turret items
            turret_items: list[LoadoutWeaponItem] = []
            for t_name in equipped_turrets:
                item = await item_repo.get_by_name(db, t_name, item_type="turret_weapon")
                if item is None:
                    item = await item_repo.get_by_name(db, t_name)
                dps = getattr(item, "dps", None) if item else None
                if dps:
                    total_dps += dps
                turret_items.append(
                    LoadoutWeaponItem(
                        name=t_name,
                        emoji=item.emoji if item else None,
                        dps=dps,
                        value=item.value if item else None,
                    )
                )

            # Build module items and compute HP
            module_items: list[LoadoutModuleItem] = []
            armor_bonus = 0
            shield_hp = 0
            for m_name in equipped_modules:
                mod_result = await db.execute(select(Module).where(Module.name == m_name))
                mod = mod_result.scalars().first()
                if mod is None:
                    item = await item_repo.get_by_name(db, m_name, item_type="module")
                    mod = item

                if mod:
                    extra = mod.extra_atts or {}
                    # ArmourModule: extra_atts has 'armour' key
                    if isinstance(extra, dict):
                        armor_bonus += int(extra.get("armour", 0))
                        shield_hp += int(extra.get("shield", 0))
                    module_items.append(
                        LoadoutModuleItem(
                            name=m_name,
                            emoji=mod.emoji,
                            type=mod.type,
                            value=mod.value,
                            tech_level=mod.tech_level,
                        )
                    )
                else:
                    module_items.append(LoadoutModuleItem(name=m_name))

            armor_hp = base_armour + armor_bonus
            total_hp = armor_hp + shield_hp
            total_value = (
                sum(w.value or 0 for w in weapon_items)
                + sum(m.value or 0 for m in module_items)
                + sum(t.value or 0 for t in turret_items)
            )

            # Build cargo list if requested
            cargo_items: list[CargoItem] = []
            if include_cargo:
                from persist.repositories.inventory_repository import InventoryRepository

                inventory_repo = InventoryRepository()
                inventory_items = await inventory_repo.get_player_items(db, player_id)
                for inv_item in inventory_items:
                    # Look up item emoji from the item repository
                    item_emoji = None
                    game_item = await item_repo.get_by_name(db, inv_item.item_name)
                    if game_item:
                        item_emoji = game_item.emoji
                    cargo_items.append(
                        CargoItem(
                            item_name=inv_item.item_name,
                            item_type=inv_item.item_type,
                            quantity=inv_item.quantity,
                            emoji=item_emoji,
                        )
                    )

            return PlayerLoadoutResponse(
                player_id=player.id,
                ship_name=ship_name,
                ship_emoji=ship_emoji,
                ship_nickname=player_ship.nickname,
                armor_hp=armor_hp,
                shield_hp=shield_hp,
                total_hp=total_hp,
                total_dps=round(total_dps, 1),
                weapons=weapon_items,
                modules=module_items,
                turrets=turret_items,
                total_value=total_value,
                cargo=cargo_items,
            )

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error getting loadout for player {player_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get player loadout"
        ) from e


@router.put("/{guild_id}/{user_id}/cooldown/reset")
async def reset_player_cooldown(
    guild_id: int,
    user_id: int,
    player_service: PlayerService = Depends(get_player_service),
):
    """Reset the bounty check cooldown for a player identified by guild_id and Discord user_id.

    Used by admins to immediately unblock a player's cooldown.
    """
    flogger.info(f"Resetting bounty cooldown for user {user_id} in guild {guild_id}")

    try:
        async with get_db_session() as db:
            # Resolve by guild + discord user → player
            from persist.repositories.player_repository import PlayerRepository as _PlayerRepo
            from persist.repositories.user_repository import UserRepository

            user_repo = UserRepository()
            player_repo = _PlayerRepo()

            user = await user_repo.get_by_id(db, user_id)
            if not user:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User {user_id} not found")

            player = await player_repo.get_by_user_and_guild(db, user.id, guild_id)
            if not player:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Player not found for user {user_id} in guild {guild_id}",
                )

            player.bounty_cooldown_end = None
            await db.commit()
            flogger.info(f"Cooldown reset for player {player.id} (user {user_id} guild {guild_id})")
            return {"status": "success", "message": f"Cooldown reset for player {player.id}"}

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error resetting cooldown for user {user_id} in guild {guild_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to reset cooldown") from e


@router.post("/transfer", response_model=TransferCreditsResponse)
async def transfer_credits(
    request: TransferCreditsRequest,
    player_service: PlayerService = Depends(get_player_service),
):
    """Transfer credits between players."""
    flogger.info(
        f"Transferring {request.amount} credits from player "
        f"{request.source_player_id} to player {request.target_player_id}"
    )

    try:
        async with get_db_session() as db:
            result = await player_service.transfer_credits(
                db,
                request.source_player_id,
                request.target_player_id,
                request.amount,
            )
            return TransferCreditsResponse(**result)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        flogger.error(f"Error transferring credits: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to transfer credits"
        ) from e
