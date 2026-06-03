"""combat_log router — read API for the /combat-log Discord command.

Endpoints:
  GET /api/v1/combat-log                        → list[CombatLogListItem]
  GET /api/v1/combat-log/{battle_id}?user_id=   → CombatLogDetail (404 if not a combatant)
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from persist.database.manager import get_db_session
from services.combat_log_service import CombatLogService
from shared import bblogger

from api.schemas.combat_log_schema import CombatLogDetail, CombatLogListItem

flogger = bblogger.get_logger("combat-log-router")


def get_combat_log_service() -> CombatLogService:
    return CombatLogService()


router = APIRouter(
    prefix="/combat-log",
    tags=["combat-log"],
    responses={404: {"description": "Not found"}},
)


# ---------------------------------------------------------------------------
# GET /combat-log
# ---------------------------------------------------------------------------


@router.get("", response_model=list[CombatLogListItem])
async def list_combat_log(
    user_id: int = Query(..., description="Discord user ID of the requesting player"),
    guild_id: int = Query(..., description="Guild to scope the search to"),
    limit: int = Query(25, ge=1, le=25, description="Max results (Discord autocomplete cap)"),
    service: CombatLogService = Depends(get_combat_log_service),
):
    """Return the most recent fights for a player in a guild.

    Results are ordered newest-first.  Each item carries the opponent name,
    the invoker's POV outcome (won/lost/stalemate), and a disambiguation ordinal
    for same-opponent same-day collisions.  Used to populate the /combat-log
    Discord autocomplete.
    """
    flogger.info(f"GET /combat-log list: user_id={user_id} guild_id={guild_id} limit={limit}")
    async with get_db_session() as db:
        try:
            items = await service.list_for_player(db, user_id=user_id, guild_id=guild_id, limit=limit)
            flogger.debug(f"GET /combat-log list: found {len(items)} items for user_id={user_id}")
            return [CombatLogListItem(**item) for item in items]
        except Exception as exc:
            flogger.error(f"GET /combat-log list failed: user_id={user_id} guild_id={guild_id}: {exc}")
            raise HTTPException(status_code=500, detail="Failed to retrieve combat log") from exc


# ---------------------------------------------------------------------------
# GET /combat-log/{battle_id}
# ---------------------------------------------------------------------------


@router.get("/{battle_id}", response_model=CombatLogDetail)
async def get_combat_log_detail(
    battle_id: int,
    user_id: int = Query(..., description="Discord user ID of the requesting player"),
    service: CombatLogService = Depends(get_combat_log_service),
):
    """Return full combat detail for a single battle.

    Returns 404 when the battle does not exist OR when user_id is not one of the
    two combatants.  We never distinguish the two cases (don't leak existence).
    """
    flogger.info(f"GET /combat-log/{battle_id}: user_id={user_id}")
    async with get_db_session() as db:
        try:
            detail = await service.get_detail(db, battle_id=battle_id, user_id=user_id)
            flogger.debug(f"GET /combat-log/{battle_id}: success for user_id={user_id}")
            return CombatLogDetail(**detail)
        except KeyError as exc:
            flogger.info(f"GET /combat-log/{battle_id}: 404 for user_id={user_id}: {exc}")
            raise HTTPException(status_code=404, detail="Battle not found") from exc
        except Exception as exc:
            flogger.error(f"GET /combat-log/{battle_id} failed: user_id={user_id}: {exc}")
            raise HTTPException(status_code=500, detail="Failed to retrieve combat detail") from exc
