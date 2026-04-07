"""
Bounty API router for the BountyBot system.

Handles bounty-related operations including:
- Checking a system against active bounties
- Listing active bounties (player-facing)
- Getting bounty route with checked status
- Spawning new bounties (admin)
- Getting criminal ship loadout
- Rendering a route map image (PNG)
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from persist.database.manager import get_db_session
from persist.repositories.bounty_repository import BountyRepository
from persist.repositories.config_repository import ConfigRepository
from services.audit_service import AuditService
from services.bounty_service import BountyService
from services.map_renderer import MapRenderer
from services.system_graph_service import SystemGraphService
from services.temperature_service import TemperatureService
from shared import bblogger

from api.schemas.bounty_schema import (
    AdminSpawnResponse,
    BountyCheckRequest,
    BountyCheckResponse,
    BountyCreateRequest,
    BountyPublicResponse,
    BountyResponse,
    ClearBountiesResponse,
)

flogger = bblogger.get_logger("bounty-router")

# ---------------------------------------------------------------------------
# Module-level singletons — created once, reused across requests.
# ---------------------------------------------------------------------------

_map_renderer = MapRenderer()
_system_graph = SystemGraphService()

# Simple in-process cache: (bounty_id, route_tuple) -> PNG bytes
_map_cache: dict[tuple[int, tuple[str, ...]], bytes] = {}


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
    from services.bounty_service import CheckResult

    flogger.info(
        f"Bounty check request: player_id={request.player_id} system={request.system_name!r} guild_id={guild_id}"
    )
    try:
        async with get_db_session() as db:
            result = await service.check_bounty(db, request.player_id, request.system_name, guild_id)
        flogger.info(
            f"Bounty check result: player_id={request.player_id}"
            f" system={request.system_name!r} result={result.result.value}"
            f" bounty_id={result.bounty_id}"
        )
        return BountyCheckResponse(
            result=result.result.value,
            bounty_id=result.bounty_id,
            message=result.message,
            new_tier=result.new_tier,
        )
    except HTTPException:
        raise
    except Exception as e:
        flogger.error(
            f"Bounty check failed: player_id={request.player_id}"
            f" system={request.system_name!r} guild_id={guild_id}: {e}"
        )
        # Return a graceful not-found response rather than propagating as a 500
        return BountyCheckResponse(
            result=CheckResult.NOT_FOUND.value,
            bounty_id=None,
            message="No active bounties found or an error occurred processing the check.",
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
            bounties = await service.bounty_repo.get_active_by_guild_and_division(db, guild_id, division)
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
    flogger.info(
        f"Bounty spawn request: guild_id={request.guild_id} division={request.division} tech_level={request.tech_level}"
    )
    try:
        async with get_db_session() as db:
            bounty = await service.spawn_bounty(db, request.guild_id, request.division, request.tech_level)
        if bounty is None:
            flogger.error(
                f"Bounty spawn failed: guild_id={request.guild_id}"
                f" division={request.division} (no criminals or systems available)"
            )
            raise HTTPException(
                status_code=400,
                detail="Failed to spawn bounty (no criminals or systems available)",
            )
        flogger.info(
            f"Bounty spawned: id={bounty.id} guild_id={request.guild_id}"
            f" division={request.division} criminal={bounty.criminal_name}"
        )
        return BountyResponse.model_validate(bounty)
    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Bounty spawn error: guild_id={request.guild_id} division={request.division}: {e}")
        raise


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


# ---------------------------------------------------------------------------
# GET /bounties/{bounty_id}/map
# ---------------------------------------------------------------------------


@router.get("/{bounty_id}/map", response_class=Response)
async def get_bounty_map(
    bounty_id: int,
    service: BountyService = Depends(get_bounty_service),
):
    """Return a PNG image of the star map with the bounty route overlaid."""
    async with get_db_session() as db:
        bounty = await service.bounty_repo.get_by_id(db, bounty_id)
        if bounty is None:
            raise HTTPException(status_code=404, detail="Bounty not found")

        route: list[str] = list(bounty.route) if bounty.route else []
        cache_key = (bounty_id, tuple(route))

        if cache_key in _map_cache:
            flogger.debug(f"Map cache hit for bounty_id={bounty_id}")
        else:
            flogger.debug(f"Map cache miss for bounty_id={bounty_id}, rendering")
            # Ensure system graph is populated.
            if not _system_graph.is_loaded():
                await _system_graph.load_graph(db)

            try:
                png_bytes = _map_renderer.render_route_for_bounty(route, _system_graph)
                _map_cache[cache_key] = png_bytes
                flogger.info(f"Map rendered for bounty_id={bounty_id} route={len(route)} systems")
            except Exception as e:
                flogger.error(f"Map render failed for bounty_id={bounty_id}: {e}")
                raise

        return Response(content=_map_cache[cache_key], media_type="image/png")


# ---------------------------------------------------------------------------
# DELETE /bounties/guild/{guild_id}/clear  (admin)
# ---------------------------------------------------------------------------


@router.delete("/guild/{guild_id}/clear", response_model=ClearBountiesResponse)
async def clear_guild_bounties(
    guild_id: int,
    tier: str | None = Query(None, description="Division tier to clear: bronze, silver, or gold"),
    user_id: int = Query(..., description="Admin Discord user ID for audit log"),
    service: BountyService = Depends(get_bounty_service),
):
    """Clear all active bounties for a guild (admin endpoint).

    Optionally filter by tier. Records an admin audit log entry.
    """
    flogger.info(f"Admin clear bounties: guild_id={guild_id} tier={tier} user_id={user_id}")
    try:
        async with get_db_session() as db:
            result = await service.clear_bounties(db, guild_id, tier)

            # Audit log
            await AuditService.log_action(
                db,
                user_id=user_id,
                action="clear_bounties",
                guild_id=guild_id,
                resource_type="bounty",
                resource_id=str(guild_id),
                details={"tier": tier, "cleared_count": result["cleared_count"], "bounty_ids": result["bounty_ids"]},
            )

            return ClearBountiesResponse(
                guild_id=result["guild_id"],
                tier=result["tier"],
                cleared_count=result["cleared_count"],
                bounty_ids=result["bounty_ids"],
                announcements_deleted=result["announcements_deleted"],
            )
    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error clearing bounties for guild {guild_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear bounties") from e


# ---------------------------------------------------------------------------
# POST /bounties/guild/{guild_id}/admin-spawn  (admin)
# ---------------------------------------------------------------------------


@router.post("/guild/{guild_id}/admin-spawn", response_model=AdminSpawnResponse)
async def admin_spawn_bounties(
    guild_id: int,
    tier: str | None = Query(None, description="Division tier to spawn: bronze, silver, or gold"),
    user_id: int = Query(..., description="Admin Discord user ID for audit log"),
    service: BountyService = Depends(get_bounty_service),
):
    """Admin-triggered bounty spawn, respecting per-guild config and temperature.

    For each tier (or the specified tier):
    1. Load guild config for max per tier, expiry, and temperatures.
    2. Count active bounties.
    3. Compute effective_max = min(guild_max, TemperatureService.get_max_bounties(temp)).
    4. If at capacity: add to skipped_tiers.
    5. Else: spawn a bounty with the configured expiry.
    6. Schedule expiry job and post Discord announcement (best-effort, non-fatal).
    """
    from utils.executors.bounty_spawn_executor import _announce_bounty, _schedule_expiry_job

    flogger.info(f"Admin spawn bounties: guild_id={guild_id} tier={tier} user_id={user_id}")

    tiers_to_process = [tier] if tier else ["bronze", "silver", "gold"]
    spawned_bounties: list[BountyResponse] = []
    skipped_tiers: list[str] = []
    errors: list[str] = []

    try:
        async with get_db_session() as db:
            # Load guild config
            config_repo = ConfigRepository()
            config = await config_repo.get_by_guild_id(db, guild_id)

            bounty_max_per_tier: dict[str, int] = (
                config.bounty_max_per_tier
                if config and config.bounty_max_per_tier
                else {"bronze": 3, "silver": 3, "gold": 3}
            )
            bounty_expiry_minutes: int = (
                config.bounty_expiry_minutes if config and config.bounty_expiry_minutes is not None else 480
            )
            division_temperatures: dict[str, float] = (
                config.division_temperatures if config and config.division_temperatures else {}
            )

            bounty_repo = BountyRepository()

            for t in tiers_to_process:
                t_lower = t.lower()
                try:
                    guild_max = bounty_max_per_tier.get(t_lower, 3)
                    temp = division_temperatures.get(t_lower, 1.0)
                    effective_max = min(guild_max, TemperatureService.get_max_bounties(temp))

                    active_count = await bounty_repo.count_active_by_guild_and_division(db, guild_id, t_lower)

                    if active_count >= effective_max:
                        flogger.debug(
                            f"Admin spawn: guild={guild_id} tier={t_lower}: "
                            f"{active_count}/{effective_max} at capacity, skipping"
                        )
                        skipped_tiers.append(t_lower)
                        continue

                    bounty = await service.spawn_bounty(db, guild_id, t_lower, expiry_minutes=bounty_expiry_minutes)
                    if bounty is None:
                        errors.append(f"Failed to spawn bounty for tier={t_lower}: no criminals or route available")
                    else:
                        spawned_bounties.append(BountyResponse.model_validate(bounty))
                        flogger.info(f"Admin spawned bounty {bounty.id} for guild={guild_id} tier={t_lower}")

                        # Schedule expiry job (best-effort — non-fatal if it fails)
                        try:
                            await _schedule_expiry_job(f"admin-spawn-{guild_id}", bounty)
                        except Exception as sched_exc:
                            flogger.warning(
                                f"Admin spawn: non-fatal failure scheduling expiry for bounty {bounty.id}: {sched_exc}"
                            )

                        # Post Discord announcement (best-effort — non-fatal if it fails)
                        try:
                            await _announce_bounty(f"admin-spawn-{guild_id}", bounty, config, db)
                        except Exception as ann_exc:
                            flogger.warning(f"Admin spawn: non-fatal failure announcing bounty {bounty.id}: {ann_exc}")

                except Exception as e:
                    flogger.error(f"Admin spawn error for guild={guild_id} tier={t_lower}: {e}")
                    errors.append(f"Error spawning tier={t_lower}: {e}")

            # Audit log
            await AuditService.log_action(
                db,
                user_id=user_id,
                action="admin_spawn_bounties",
                guild_id=guild_id,
                resource_type="bounty",
                resource_id=str(guild_id),
                details={
                    "tier": tier,
                    "spawned_count": len(spawned_bounties),
                    "skipped_tiers": skipped_tiers,
                    "errors": errors,
                },
            )

        return AdminSpawnResponse(
            guild_id=guild_id,
            spawned=spawned_bounties,
            skipped_tiers=skipped_tiers,
            errors=errors,
        )

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error in admin-spawn for guild {guild_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to admin-spawn bounties") from e
