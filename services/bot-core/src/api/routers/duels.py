"""
Duel API router for the BountyBot system.

Handles duel (PvP challenge) lifecycle operations including:
- Creating a new duel challenge
- Accepting a pending duel (resolves combat, transfers credits)
- Rejecting a pending duel
- Listing pending duels for a user (for autocomplete)
"""

import os
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from persist.database.manager import get_db_session
from services.audit_service import AuditService
from services.duel_service import DuelService
from shared import bblogger

from api.schemas.duel_schema import DuelRequestCreate, DuelRequestResponse

flogger = bblogger.get_logger("duel-router")

_GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
_GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
_GATEWAY_BASE_URL = f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}/api/v1"
_INTERNAL_AUTH_TOKEN = os.getenv("INTERNAL_AUTH_TOKEN", "")


async def _push_duel_caches_for_players(
    db,
    service: "DuelService",
    guild_id: int,
    challenger_id: int | None = None,
    target_id: int | None = None,
) -> None:
    """Fetch fresh duel lists for affected players and push to gateway cache.

    Non-fatal: any failure is logged as WARNING and silently swallowed.
    Called after each successful duel mutation (challenge/accept/reject/cancel).
    """
    try:
        if challenger_id is not None:
            try:
                challenger_outgoing = await service.get_outgoing_for_challenger(db, challenger_id, guild_id)
                outgoing_dicts = [
                    DuelRequestResponse.model_validate(d).model_dump(mode="json") for d, _ in challenger_outgoing
                ]
            except Exception:  # pylint: disable=broad-exception-caught
                outgoing_dicts = []
            try:
                challenger_pending = await service.get_pending_for_target(db, challenger_id, guild_id)
                pending_dicts = [
                    DuelRequestResponse.model_validate(d).model_dump(mode="json") for d, _ in challenger_pending
                ]
            except Exception:  # pylint: disable=broad-exception-caught
                pending_dicts = []
            await _push_duel_cache(guild_id, challenger_id, pending_dicts, outgoing_dicts)

        if target_id is not None:
            try:
                target_pending = await service.get_pending_for_target(db, target_id, guild_id)
                pending_dicts = [
                    DuelRequestResponse.model_validate(d).model_dump(mode="json") for d, _ in target_pending
                ]
            except Exception:  # pylint: disable=broad-exception-caught
                pending_dicts = []
            try:
                target_outgoing = await service.get_outgoing_for_challenger(db, target_id, guild_id)
                outgoing_dicts = [
                    DuelRequestResponse.model_validate(d).model_dump(mode="json") for d, _ in target_outgoing
                ]
            except Exception:  # pylint: disable=broad-exception-caught
                outgoing_dicts = []
            await _push_duel_cache(guild_id, target_id, pending_dicts, outgoing_dicts)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"_push_duel_caches_for_players: failed for guild={guild_id}: {type(exc).__name__}: {exc}")


async def _push_duel_cache(guild_id: int, player_id: int, pending_duels: list, outgoing_duels: list) -> None:
    """Push updated duel lists to the gateway's DuelCog autocomplete cache.

    Non-fatal: exceptions are caught and logged as WARNING — never blocks the primary operation.
    Uses a 5-second timeout; fire-and-forget pattern.
    """

    import httpx

    try:
        headers = {}
        if _INTERNAL_AUTH_TOKEN:
            headers["x-internal-auth"] = _INTERNAL_AUTH_TOKEN

        # SSRF guard: coerce IDs to int so any non-numeric value (path-traversal
        # payload, injection attempt) raises ValueError before the URL is built.
        safe_guild = int(guild_id)
        safe_player = int(player_id)
        cache_url = (
            f"{_GATEWAY_BASE_URL}/internal/autocomplete/duel-cache"
            f"/{quote(str(safe_guild), safe='')}/{quote(str(safe_player), safe='')}"
        )
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                cache_url,
                json={"pending_duels": pending_duels, "outgoing_duels": outgoing_duels},
                headers=headers,
                timeout=5,
            )
        resp.raise_for_status()
        flogger.debug(
            f"_push_duel_cache: pushed guild={guild_id} player={player_id} "
            f"pending={len(pending_duels)} outgoing={len(outgoing_duels)}"
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(
            f"_push_duel_cache: failed for guild={guild_id} player={player_id}: {type(exc).__name__}: {exc}"
        )


def get_duel_service() -> DuelService:
    return DuelService()


router = APIRouter(
    prefix="/duels",
    tags=["duels"],
    responses={404: {"description": "Duel not found"}},
)


# ---------------------------------------------------------------------------
# GET /duels/outgoing
# ---------------------------------------------------------------------------


@router.get("/outgoing", response_model=list[DuelRequestResponse])
async def get_outgoing_duels(
    user_id: int,
    guild_id: int,
    service: DuelService = Depends(get_duel_service),
):
    """Get pending duel requests where the user is the challenger (for /duel-cancel autocomplete)."""
    flogger.info(f"Get outgoing duels request: user_id={user_id} guild_id={guild_id}")
    async with get_db_session() as db:
        try:
            duels_with_names = await service.get_outgoing_for_challenger(db, user_id, guild_id)
            flogger.debug(f"Retrieved {len(duels_with_names)} outgoing duels for user_id={user_id} guild_id={guild_id}")
            result = []
            for duel, target_name in duels_with_names:
                resp = DuelRequestResponse.model_validate(duel)
                resp.target_name = target_name
                result.append(resp)
            return result
        except Exception as exc:
            flogger.error(f"Get outgoing duels failed for user_id={user_id} guild_id={guild_id}: {exc}")
            raise HTTPException(status_code=500, detail="Failed to retrieve outgoing duels") from exc


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
    flogger.info(f"Get pending duels request: user_id={user_id} guild_id={guild_id}")
    async with get_db_session() as db:
        try:
            duels_with_names = await service.get_pending_for_target(db, user_id, guild_id)
            flogger.debug(f"Retrieved {len(duels_with_names)} pending duels for user_id={user_id} guild_id={guild_id}")
            result = []
            for duel, challenger_name in duels_with_names:
                resp = DuelRequestResponse.model_validate(duel)
                resp.challenger_name = challenger_name
                result.append(resp)
            return result
        except Exception as exc:
            flogger.error(f"Get pending duels failed for user_id={user_id} guild_id={guild_id}: {exc}")
            raise HTTPException(status_code=500, detail="Failed to retrieve pending duels") from exc


# ---------------------------------------------------------------------------
# POST /duels/challenge
# ---------------------------------------------------------------------------


@router.post("/challenge", response_model=DuelRequestResponse)
async def create_challenge(
    request: DuelRequestCreate,
    service: DuelService = Depends(get_duel_service),
):
    """Create a new duel challenge between two players."""
    flogger.info(
        f"Duel challenge request: challenger={request.challenger_id}"
        f" target={request.target_id} stakes={request.stakes} guild_id={request.guild_id}"
    )
    flogger.debug(
        f"Challenge payload: challenger_id={request.challenger_id}"
        f" target_id={request.target_id} stakes={request.stakes}"
        f" guild_id={request.guild_id}"
    )
    async with get_db_session() as db:
        try:
            duel = await service.create_challenge(
                db,
                request.challenger_id,
                request.target_id,
                request.stakes,
                request.guild_id,
            )
            flogger.debug(
                f"Challenge response: duel_id={duel.id} status={duel.status}"
                f" created_at={duel.created_at} expires_at={duel.expires_at}"
            )
            flogger.info(
                f"Duel challenge created: duel_id={duel.id}"
                f" challenger={request.challenger_id} target={request.target_id}"
                f" stakes={request.stakes}"
            )
            result = DuelRequestResponse.model_validate(duel)

            # Non-fatal gateway cache push for both affected players
            await _push_duel_caches_for_players(
                db,
                service,
                request.guild_id,
                challenger_id=request.challenger_id,
                target_id=request.target_id,
            )
            return result
        except ValueError as exc:
            flogger.error(
                f"Duel challenge failed: challenger={request.challenger_id} target={request.target_id}: {exc}"
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            flogger.error(
                f"Unexpected error during duel challenge: challenger={request.challenger_id}"
                f" target={request.target_id}: {exc}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="An internal error occurred while processing the duel challenge.",
            ) from exc


# ---------------------------------------------------------------------------
# POST /duels/{duel_id}/accept
# ---------------------------------------------------------------------------


@router.post("/{duel_id}/accept")
async def accept_duel(
    duel_id: int,
    user_id: int = Query(..., description="ID of the user accepting the duel"),
    service: DuelService = Depends(get_duel_service),
):
    """Accept a pending duel and resolve combat.

    Only the challenged player (target) may accept.
    """
    async with get_db_session() as db:
        # Authorization: verify caller is the duel target
        try:
            duel = await service.get_duel(db, duel_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        if user_id != duel.target_id:
            raise HTTPException(
                status_code=403,
                detail="Only the challenged player can accept/reject this duel.",
            )

        flogger.info(f"Duel accept request: duel_id={duel_id} user_id={user_id}")
        flogger.debug(f"User {user_id} is accepting duel {duel_id} (authorization check passed)")
        try:
            result = await service.accept_duel(db, duel_id)
        except ValueError as exc:
            msg = str(exc)
            flogger.error(f"Duel accept failed: duel_id={duel_id} user_id={user_id}: {msg}")
            status_code = 404 if "not found" in msg.lower() else 400
            raise HTTPException(status_code=status_code, detail=msg) from exc
        except Exception as exc:
            flogger.error(
                f"Unexpected error during duel accept: duel_id={duel_id} user_id={user_id}: {exc}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="An internal error occurred while processing the duel acceptance.",
            ) from exc

        fight = result["fight_results"]
        challenger = result["challenger"]
        target = result["target"]

        flogger.debug(
            f"Duel resolution details: duel_id={duel_id}"
            f" challenger_id={challenger.id} target_id={target.id}"
            f" challenger_hp={fight.ship1_stats.varied_hp} target_hp={fight.ship2_stats.varied_hp}"
            f" challenger_dps={fight.ship1_stats.varied_dps} target_dps={fight.ship2_stats.varied_dps}"
        )

        flogger.info(
            f"Duel accepted and resolved: duel_id={duel_id}"
            f" winner={fight.winner_name!r} stalemate={fight.is_stalemate}"
            f" credits_transferred={result['credits_transferred']}"
        )

        # Non-fatal cache push for both affected players
        await _push_duel_caches_for_players(
            db,
            service,
            duel.guild_id,
            challenger_id=challenger.id,
            target_id=target.id,
        )

        return {
            "duel_id": duel_id,
            "is_stalemate": fight.is_stalemate,
            "winner_name": fight.winner_name,
            "loser_name": fight.loser_name,
            "credits_transferred": result["credits_transferred"],
            "stakes": result["stakes"],
            "challenger_id": challenger.id,
            "challenger_name": result.get("challenger_name"),
            "challenger_credits": challenger.credits,
            "challenger_hp": fight.ship1_stats.varied_hp,
            "challenger_dps": fight.ship1_stats.varied_dps,
            "target_id": target.id,
            "target_name": result.get("target_name"),
            "target_credits": target.credits,
            "target_hp": fight.ship2_stats.varied_hp,
            "target_dps": fight.ship2_stats.varied_dps,
        }


# ---------------------------------------------------------------------------
# POST /duels/{duel_id}/reject
# ---------------------------------------------------------------------------


@router.post("/{duel_id}/reject", response_model=DuelRequestResponse)
async def reject_duel(
    duel_id: int,
    user_id: int = Query(..., description="ID of the user rejecting the duel"),
    service: DuelService = Depends(get_duel_service),
):
    """Reject a pending duel challenge.

    Only the challenged player (target) may reject.
    """
    async with get_db_session() as db:
        # Authorization: verify caller is the duel target
        try:
            duel = await service.get_duel(db, duel_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        if user_id != duel.target_id:
            raise HTTPException(
                status_code=403,
                detail="Only the challenged player can accept/reject this duel.",
            )

        flogger.info(f"Duel reject request: duel_id={duel_id} user_id={user_id}")
        flogger.debug(f"User {user_id} is rejecting duel {duel_id} (authorization check passed)")
        try:
            updated = await service.reject_duel(db, duel_id)
            flogger.debug(
                f"Duel rejection payload: duel_id={duel_id} status={updated.status}"
                f" challenger_id={updated.challenger_id} target_id={updated.target_id}"
            )
            flogger.info(f"Duel rejected: duel_id={duel_id} user_id={user_id}")

            # Non-fatal cache push for both affected players (rejecter=target, challenger)
            await _push_duel_caches_for_players(
                db,
                service,
                updated.guild_id,
                challenger_id=updated.challenger_id,
                target_id=updated.target_id,
            )
            return DuelRequestResponse.model_validate(updated)
        except ValueError as exc:
            msg = str(exc)
            flogger.error(f"Duel reject failed: duel_id={duel_id} user_id={user_id}: {msg}")
            status_code = 404 if "not found" in msg.lower() else 400
            raise HTTPException(status_code=status_code, detail=msg) from exc
        except Exception as exc:
            flogger.error(
                f"Unexpected error during duel reject: duel_id={duel_id} user_id={user_id}: {exc}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="An internal error occurred while processing the duel rejection.",
            ) from exc


# ---------------------------------------------------------------------------
# POST /duels/{duel_id}/cancel  (B.64 — challenger self-cancel)
# ---------------------------------------------------------------------------


@router.post("/{duel_id}/cancel", response_model=DuelRequestResponse)
async def cancel_duel(
    duel_id: int,
    user_id: int = Query(..., description="ID of the player cancelling the duel (must be the challenger)"),
    service: DuelService = Depends(get_duel_service),
):
    """Cancel a pending duel challenge.

    Only the challenger (the player who issued the challenge) may cancel via this endpoint.
    """
    flogger.info(f"Duel cancel request: duel_id={duel_id} user_id={user_id}")
    async with get_db_session() as db:
        try:
            updated = await service.cancel_duel(db, duel_id, requesting_player_id=user_id)
            flogger.debug(
                f"Duel cancel payload: duel_id={duel_id} status={updated.status}"
                f" challenger_id={updated.challenger_id} target_id={updated.target_id}"
            )
            flogger.info(f"Duel cancelled: duel_id={duel_id} user_id={user_id}")

            # Non-fatal cache push for both affected players (canceller=challenger, target)
            await _push_duel_caches_for_players(
                db,
                service,
                updated.guild_id,
                challenger_id=updated.challenger_id,
                target_id=updated.target_id,
            )
            return DuelRequestResponse.model_validate(updated)
        except ValueError as exc:
            msg = str(exc)
            flogger.error(f"Duel cancel failed: duel_id={duel_id} user_id={user_id}: {msg}")
            status_code = 404 if "not found" in msg.lower() else 400
            raise HTTPException(status_code=status_code, detail=msg) from exc
        except Exception as exc:
            flogger.error(
                f"Unexpected error during duel cancel: duel_id={duel_id} user_id={user_id}: {exc}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="An internal error occurred while processing the duel cancellation.",
            ) from exc


# ---------------------------------------------------------------------------
# GET /duels/pending-all  — all pending duels for a guild (admin autocomplete)
# ---------------------------------------------------------------------------


@router.get("/pending-all", response_model=list[DuelRequestResponse])
async def get_all_pending_duels(
    guild_id: int,
    service: DuelService = Depends(get_duel_service),
):
    """Get ALL pending duels for a guild, regardless of challenger or target.

    Used by the Discord gateway admin autocomplete for /admin_duel so the
    admin can see every open duel and pick one (or "all") to cancel.
    Both challenger_name and target_name are populated on each response.
    """
    flogger.info(f"Get all pending duels request: guild_id={guild_id}")
    async with get_db_session() as db:
        try:
            duels_with_names = await service.get_all_pending_for_guild(db, guild_id)
            flogger.debug(f"Retrieved {len(duels_with_names)} pending duels for guild_id={guild_id}")
            result = []
            for duel, challenger_name, target_name in duels_with_names:
                resp = DuelRequestResponse.model_validate(duel)
                resp.challenger_name = challenger_name
                resp.target_name = target_name
                result.append(resp)
            return result
        except Exception as exc:
            flogger.error(f"Get all pending duels failed for guild_id={guild_id}: {exc}")
            raise HTTPException(status_code=500, detail="Failed to retrieve pending duels") from exc


# ---------------------------------------------------------------------------
# POST /duels/admin-cancel-all  — cancel ALL pending duels for a guild
# ---------------------------------------------------------------------------


@router.post("/admin-cancel-all")
async def admin_cancel_all_duels(
    guild_id: int = Query(..., description="Guild whose pending duels should all be cancelled"),
    admin_user_id: int = Query(..., description="Discord user ID of the admin performing the action"),
    service: DuelService = Depends(get_duel_service),
):
    """Admin bulk-cancel: cancel ALL pending duels for a guild in one call."""
    flogger.info(f"Admin cancel-all duels request: guild_id={guild_id} by admin={admin_user_id}")
    async with get_db_session() as db:
        try:
            cancelled = await service.cancel_all_pending_duels(db, guild_id)
            duel_ids = [d.id for d in cancelled]
            count = len(cancelled)
            flogger.info(f"Admin bulk-cancelled {count} duels in guild={guild_id} by admin={admin_user_id}")
            await AuditService.log_action(
                db,
                user_id=admin_user_id,
                action="admin_cancel_all_duels",
                guild_id=guild_id,
                resource_type="duel",
                resource_id=str(guild_id),
                details={"count": count, "duel_ids": duel_ids},
            )
            # Push duel cache for all affected player pairs (best-effort, non-fatal)
            for duel in cancelled:
                await _push_duel_caches_for_players(
                    db,
                    service,
                    guild_id,
                    challenger_id=duel.challenger_id,
                    target_id=duel.target_id,
                )
            return {"cancelled_count": count, "duel_ids": duel_ids}
        except Exception as exc:
            flogger.error(f"Admin cancel-all duels failed for guild_id={guild_id}: {exc}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail="An internal error occurred while cancelling all pending duels.",
            ) from exc


# ---------------------------------------------------------------------------
# POST /duels/{duel_id}/admin-cancel  (B.65 — admin cancel, no ownership check)
# ---------------------------------------------------------------------------


@router.post("/{duel_id}/admin-cancel", response_model=DuelRequestResponse)
async def admin_cancel_duel(
    duel_id: int,
    admin_user_id: int = Query(..., description="Discord user ID of the admin performing the action"),
    service: DuelService = Depends(get_duel_service),
):
    """Admin cancel: cancel any pending duel without ownership check."""
    flogger.info(f"Admin duel cancel request: duel_id={duel_id} by admin={admin_user_id}")
    async with get_db_session() as db:
        try:
            updated = await service.cancel_duel(db, duel_id)
            flogger.info(f"Duel admin-cancelled: duel_id={duel_id} by admin={admin_user_id}")
            await AuditService.log_action(
                db,
                user_id=admin_user_id,
                action="admin_cancel_duel",
                guild_id=updated.guild_id,
                resource_type="duel",
                resource_id=str(duel_id),
                details={
                    "challenger_id": updated.challenger_id,
                    "target_id": updated.target_id,
                    "stakes": updated.stakes,
                },
            )
            # Push duel cache for both affected players (mirrors user-facing cancel_duel)
            await _push_duel_caches_for_players(
                db,
                service,
                updated.guild_id,
                challenger_id=updated.challenger_id,
                target_id=updated.target_id,
            )
            return DuelRequestResponse.model_validate(updated)
        except ValueError as exc:
            msg = str(exc)
            flogger.error(f"Admin duel cancel failed: duel_id={duel_id}: {msg}")
            status_code = 404 if "not found" in msg.lower() else 400
            raise HTTPException(status_code=status_code, detail=msg) from exc
        except Exception as exc:
            flogger.error(
                f"Unexpected error during admin duel cancel: duel_id={duel_id}: {exc}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="An internal error occurred while processing the admin duel cancellation.",
            ) from exc
