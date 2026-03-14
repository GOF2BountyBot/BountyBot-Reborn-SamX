"""
Duel API router for the BountyBot system.

Handles duel (PvP challenge) lifecycle operations including:
- Creating a new duel challenge
- Accepting a pending duel (resolves combat, transfers credits)
- Rejecting a pending duel
- Listing pending duels for a user (for autocomplete)
"""

from fastapi import APIRouter, Depends, HTTPException
from persist.database.manager import get_db_session
from services.duel_service import DuelService
from shared import bblogger

from api.schemas.duel_schema import DuelRequestCreate, DuelRequestResponse

flogger = bblogger.get_logger("duel-router")


def get_duel_service() -> DuelService:
    return DuelService()


router = APIRouter(
    prefix="/duels",
    tags=["duels"],
    responses={404: {"description": "Duel not found"}},
)


# ---------------------------------------------------------------------------
# GET /duels/pending
# ---------------------------------------------------------------------------


@router.get("/pending", response_model=list[DuelRequestResponse])
async def get_pending_duels(
    user_id: int,
    guild_id: int,
    service: DuelService = Depends(get_duel_service),
):
    """Get pending duel requests where the user is the target (for autocomplete)."""
    async with get_db_session() as db:
        duels = await service.get_pending_for_target(db, user_id, guild_id)
        return [DuelRequestResponse.model_validate(d) for d in duels]


# ---------------------------------------------------------------------------
# POST /duels/challenge
# ---------------------------------------------------------------------------


@router.post("/challenge", response_model=DuelRequestResponse)
async def create_challenge(
    request: DuelRequestCreate,
    service: DuelService = Depends(get_duel_service),
):
    """Create a new duel challenge between two players."""
    async with get_db_session() as db:
        try:
            duel = await service.create_challenge(
                db,
                request.challenger_id,
                request.target_id,
                request.stakes,
                request.guild_id,
            )
            return DuelRequestResponse.model_validate(duel)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# POST /duels/{duel_id}/accept
# ---------------------------------------------------------------------------


@router.post("/{duel_id}/accept")
async def accept_duel(
    duel_id: int,
    service: DuelService = Depends(get_duel_service),
):
    """Accept a pending duel and resolve combat."""
    async with get_db_session() as db:
        try:
            result = await service.accept_duel(db, duel_id)
        except ValueError as exc:
            msg = str(exc)
            status_code = 404 if "not found" in msg.lower() else 400
            raise HTTPException(status_code=status_code, detail=msg) from exc

        fight = result["fight_results"]
        challenger = result["challenger"]
        target = result["target"]

        return {
            "duel_id": duel_id,
            "is_stalemate": fight.is_stalemate,
            "winner_name": fight.winner_name,
            "loser_name": fight.loser_name,
            "credits_transferred": result["credits_transferred"],
            "stakes": result["stakes"],
            "challenger_id": challenger.id,
            "challenger_credits": challenger.credits,
            "target_id": target.id,
            "target_credits": target.credits,
        }


# ---------------------------------------------------------------------------
# POST /duels/{duel_id}/reject
# ---------------------------------------------------------------------------


@router.post("/{duel_id}/reject", response_model=DuelRequestResponse)
async def reject_duel(
    duel_id: int,
    service: DuelService = Depends(get_duel_service),
):
    """Reject a pending duel challenge."""
    async with get_db_session() as db:
        try:
            duel = await service.reject_duel(db, duel_id)
            return DuelRequestResponse.model_validate(duel)
        except ValueError as exc:
            msg = str(exc)
            status_code = 404 if "not found" in msg.lower() else 400
            raise HTTPException(status_code=status_code, detail=msg) from exc
