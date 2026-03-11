"""
Players API router for the BountyBot inventory system.

Handles REST API endpoints for player management, progression, and statistics.
This router follows the requirement that all major subsystem interactions
must be done via REST API.
"""

from typing import List, Optional

from shared import bblogger
from api.schemas.players_schema import (
    CreatePlayerRequest,
    PlayerResponse,
    PlayerStatisticsResponse,
    UpdateCreditsRequest,
    UpdateXPRequest,
)
from fastapi import APIRouter, Depends, HTTPException, status
from persist.database.manager import get_db_session
from services.player_service import PlayerService

flogger = bblogger.get_logger("players-api-router")

router = APIRouter(
    prefix="/players",
    tags=["players"],
    responses={
        404: {"description": "Player not found"},
        500: {"description": "Internal server error"}
    }
)

# Dependency injection
async def get_player_service():
    return PlayerService()

@router.post("/", response_model=PlayerResponse, status_code=status.HTTP_201_CREATED)
async def create_or_get_player(
    request: CreatePlayerRequest,
    player_service: PlayerService = Depends(get_player_service)
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
                db,
                request.discord_id,
                request.guild_id,
                request.discord_username
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
                updated_at=player.updated_at.isoformat()
            )

    except Exception as e:
        flogger.error(f"Error creating/getting player: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create or get player"
        ) from e

@router.get("/{player_id}", response_model=PlayerResponse)
async def get_player(
    player_id: int,
    player_service: PlayerService = Depends(get_player_service)
):
    """Get a player by ID."""
    flogger.debug(f"Getting player: {player_id}")

    try:
        async with get_db_session() as db:
            player = await player_service.player_repo.get_by_id(db, player_id)
            if not player:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Player {player_id} not found"
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
                updated_at=player.updated_at.isoformat()
            )

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error getting player {player_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get player"
        ) from e

@router.get("/guild/{guild_id}", response_model=List[PlayerResponse])
async def get_players_by_guild(
    guild_id: int,
    skip: int = 0,
    limit: int = 100,
    tier: Optional[str] = None,
    player_service: PlayerService = Depends(get_player_service)
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
            paginated_players = players[skip:skip + limit]

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
                    updated_at=player.updated_at.isoformat()
                )
                for player in paginated_players
            ]

    except Exception as e:
        flogger.error(f"Error getting players for guild {guild_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get players"
        ) from e

@router.put("/{player_id}/credits", response_model=PlayerResponse)
async def update_player_credits(
    player_id: int,
    request: UpdateCreditsRequest,
    player_service: PlayerService = Depends(get_player_service)
):
    """Update player credits."""
    flogger.info(f"Updating credits for player {player_id}: {request.credits}")

    try:
        async with get_db_session() as db:
            player = await player_service.update_player_credits(
                db, player_id, request.credits, request.update_lifetime
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
                updated_at=player.updated_at.isoformat()
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error updating credits for player {player_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update credits"
        ) from e

@router.put("/{player_id}/xp", response_model=PlayerResponse)
async def update_player_xp(
    player_id: int,
    request: UpdateXPRequest,
    player_service: PlayerService = Depends(get_player_service)
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
                updated_at=player.updated_at.isoformat()
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error updating XP for player {player_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update XP"
        ) from e

@router.post("/{player_id}/prestige", response_model=PlayerResponse)
async def prestige_player(
    player_id: int,
    player_service: PlayerService = Depends(get_player_service)
):
    """Reset player to Bronze tier but increment prestige count."""
    flogger.info(f"Prestiging player {player_id}")

    try:
        async with get_db_session() as db:
            player = await player_service.prestige_player(db, player_id)

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
                updated_at=player.updated_at.isoformat()
            )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error prestiging player {player_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to prestige player"
        ) from e

@router.get("/{player_id}/statistics", response_model=PlayerStatisticsResponse)
async def get_player_statistics(
    player_id: int,
    player_service: PlayerService = Depends(get_player_service)
):
    """Get comprehensive player statistics."""
    flogger.debug(f"Getting statistics for player {player_id}")

    try:
        async with get_db_session() as db:
            stats = await player_service.get_player_statistics(db, player_id)
            return PlayerStatisticsResponse(**stats)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        ) from e
    except Exception as e:
        flogger.error(f"Error getting statistics for player {player_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get player statistics"
        ) from e
