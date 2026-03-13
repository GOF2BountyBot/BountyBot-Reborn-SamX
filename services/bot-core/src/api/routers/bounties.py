"""
Bounty API router for the BountyBot system.

Handles bounty-related operations including:
- Checking a system against active bounties
- Listing active bounties (player-facing)
- Getting bounty route with checked status
- Spawning new bounties (admin)
- Getting criminal ship loadout
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from persist.database.manager import get_db_session
from services.bounty_service import BountyService
from shared import bblogger

from api.schemas.bounty_schema import (
    BountyCheckRequest,
    BountyCheckResponse,
    BountyCreateRequest,
    BountyPublicResponse,
    BountyResponse,
)

flogger = bblogger.get_logger("bounty-router")


def get_bounty_service() -> BountyService:
    return BountyService()


router = APIRouter(
    prefix="/bounties",
    tags=["bounties"],
    responses={404: {"description": "Bounty not found"}},
)


# ---------------------------------------------------------------------------
# POST /bounties/check
# ---------------------------------------------------------------------------


@router.post("/check", response_model=BountyCheckResponse)
async def check_bounty(
    request: BountyCheckRequest,
    guild_id: int = Query(..., description="Discord guild ID"),
    service: BountyService = Depends(get_bounty_service),
):
    """Check a system against active bounties for a given guild."""
    async with get_db_session() as db:
        result = await service.check_bounty(
            db, request.player_id, request.system_name, guild_id
        )
        return BountyCheckResponse(
            result=result.result.value,
            bounty_id=result.bounty_id,
            message=result.message,
        )


# ---------------------------------------------------------------------------
# GET /bounties/
# ---------------------------------------------------------------------------


@router.get("/", response_model=list[BountyPublicResponse])
async def list_bounties(
    guild_id: int = Query(..., description="Discord guild ID"),
    division: str | None = Query(None, description="Filter by division"),
    service: BountyService = Depends(get_bounty_service),
):
    """List active bounties (player-facing — hides the answer)."""
    async with get_db_session() as db:
        if division:
            bounties = await service.bounty_repo.get_active_by_guild_and_division(
                db, guild_id, division
            )
        else:
            bounties = await service.bounty_repo.get_active_by_guild(db, guild_id)
        return [BountyPublicResponse.model_validate(b) for b in bounties]


# ---------------------------------------------------------------------------
# GET /bounties/{bounty_id}/route
# ---------------------------------------------------------------------------


@router.get("/{bounty_id}/route")
async def get_bounty_route(
    bounty_id: int,
    service: BountyService = Depends(get_bounty_service),
):
    """Get a bounty's route with checked status per system."""
    async with get_db_session() as db:
        bounty = await service.bounty_repo.get_by_id(db, bounty_id)
        if bounty is None:
            raise HTTPException(status_code=404, detail="Bounty not found")
        return {
            "bounty_id": bounty.id,
            "criminal_name": bounty.criminal_name,
            "route": bounty.route,
            "checked": bounty.checked,
            "status": bounty.status,
        }


# ---------------------------------------------------------------------------
# POST /bounties/spawn  (admin)
# ---------------------------------------------------------------------------


@router.post("/spawn", response_model=BountyResponse)
async def spawn_bounty(
    request: BountyCreateRequest,
    service: BountyService = Depends(get_bounty_service),
):
    """Manually spawn a new bounty (admin endpoint)."""
    async with get_db_session() as db:
        bounty = await service.spawn_bounty(
            db, request.guild_id, request.division, request.tech_level
        )
        if bounty is None:
            raise HTTPException(
                status_code=400,
                detail="Failed to spawn bounty (no criminals or systems available)",
            )
        return BountyResponse.model_validate(bounty)


# ---------------------------------------------------------------------------
# GET /bounties/{bounty_id}/loadout
# ---------------------------------------------------------------------------


@router.get("/{bounty_id}/loadout")
async def get_bounty_loadout(
    bounty_id: int,
    service: BountyService = Depends(get_bounty_service),
):
    """Get the criminal's ship loadout for a bounty."""
    async with get_db_session() as db:
        bounty = await service.bounty_repo.get_by_id(db, bounty_id)
        if bounty is None:
            raise HTTPException(status_code=404, detail="Bounty not found")
        return {
            "bounty_id": bounty.id,
            "criminal_name": bounty.criminal_name,
            "criminal_ship": bounty.criminal_ship,
            "tech_level": bounty.tech_level,
        }
