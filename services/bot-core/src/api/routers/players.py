"""
Players API router for the BountyBot inventory system.

Handles REST API endpoints for player management, progression, and statistics.
This router follows the requirement that all major subsystem interactions
must be done via REST API.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from persist.database.manager import get_db_session
from services.exceptions import GuildNotConfiguredError
from services.loadout_response_service import LoadoutResponseService
from services.player_service import PlayerService
from shared import bblogger
from sqlalchemy.exc import IntegrityError

from api.schemas.loadout_schema import LoadoutResponse
from api.schemas.players_schema import (
    CreatePlayerRequest,
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


async def get_loadout_response_service() -> LoadoutResponseService:
    return LoadoutResponseService()


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
        # Package G (B.19): wrap in db.begin() so inventory clear and ship
        # loadout clear are atomic (invariant I3).
        async with get_db_session() as db, db.begin():
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


@router.get("/{player_id}/loadout", response_model=LoadoutResponse)
async def get_player_loadout(
    player_id: int,
    include_cargo: bool = False,
    viewer_discord_id: int | None = None,
    loadout_service: LoadoutResponseService = Depends(get_loadout_response_service),
) -> LoadoutResponse:
    """Get the active ship loadout for a player.

    Returns a unified `LoadoutResponse` with `subject_kind="player"`, computed
    HP/DPS stats, per-module effects, and optional cargo (when `include_cargo=true`).

    # NOTE: `include_cargo` is caller-gated. Bot-core trusts the gateway's permission check.
    # The gateway enforces viewer == owner OR viewer is admin (see playerCog._check_is_admin).
    # See LOADOUT_EMBED_DESIGN_SPEC.md §10 item 5 (accepted internal-network trust boundary).
    """
    flogger.debug(
        f"Getting loadout for player {player_id}, include_cargo={include_cargo}, viewer_discord_id={viewer_discord_id}"
    )

    try:
        async with get_db_session() as db:
            response = await loadout_service.build_player_loadout(
                db,
                player_id,
                include_cargo=include_cargo,
                viewer_discord_id=viewer_discord_id,
            )
            if response is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Player {player_id} not found")
            return response

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
        async with get_db_session() as db, db.begin():
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
