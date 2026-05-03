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
from persist.repositories.config_repository import ConfigRepository
from services.audit_service import AuditService
from services.bounty_service import BountyService
from services.loadout_response_service import LoadoutResponseService
from services.map_renderer import MapRenderer
from services.system_graph_service import SystemGraphService
from shared import bblogger
from utils.bounty_announcement_payload import _project_checked

from api.schemas.bounty_schema import (
    AdminSpawnResponse,
    BountyCheckOutcome,
    BountyCheckRequest,
    BountyCheckResponse,
    BountyCreateRequest,
    BountyPublicResponse,
    BountyResponse,
    ClearBountiesResponse,
    CombatBonusRequest,
    CombatBonusResponse,
)
from api.schemas.loadout_schema import LoadoutResponse

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


def get_loadout_response_service() -> LoadoutResponseService:
    return LoadoutResponseService()


router = APIRouter(
    prefix="/bounties",
    tags=["bounties"],
    responses={404: {"description": "Bounty not found"}},
)


# ---------------------------------------------------------------------------
# POST /bounties/check
# ---------------------------------------------------------------------------


def _outcome_to_schema(outcome) -> BountyCheckOutcome:
    """Convert a service-layer :class:`CheckResponse` to its API schema."""
    return BountyCheckOutcome(
        result=outcome.result.value,
        bounty_id=outcome.bounty_id,
        message=outcome.message,
        new_tier=outcome.new_tier,
        division=outcome.division,
        criminal_name=outcome.criminal_name,
        reward=outcome.reward,
        combat_result=outcome.combat_result,
        combat_won=outcome.combat_won,
        bonus_won=outcome.bonus_won,
        total_reward=outcome.total_reward,
        criminal_ship=outcome.criminal_ship,
        recently_spotted=outcome.recently_spotted,
        proximity_hint=outcome.proximity_hint,
        distance_to_answer=outcome.distance_to_answer,
    )


def _build_check_response(multi) -> BountyCheckResponse:
    """Build the wire-level :class:`BountyCheckResponse` from a service
    :class:`MultiCheckResponse`.

    Top-level fields mirror ``outcomes[0]`` so single-bounty clients keep
    working unchanged. ``outcomes`` and ``result_count`` are always
    populated for new multi-bounty-aware clients.
    """
    outcome_schemas = [_outcome_to_schema(o) for o in multi.outcomes]
    first = outcome_schemas[0] if outcome_schemas else None
    return BountyCheckResponse(
        outcomes=outcome_schemas,
        result_count=len(outcome_schemas),
        result=first.result if first else "not_found",
        bounty_id=first.bounty_id if first else None,
        message=first.message if first else "",
        new_tier=first.new_tier if first else None,
        division=multi.division if multi.division is not None else (first.division if first else None),
        criminal_name=first.criminal_name if first else None,
        reward=first.reward if first else None,
        combat_result=first.combat_result if first else None,
        combat_won=first.combat_won if first else None,
        bonus_won=first.bonus_won if first else False,
        total_reward=first.total_reward if first else None,
        criminal_ship=first.criminal_ship if first else None,
        recently_spotted=first.recently_spotted if first else False,
        # cooldown_until is only populated on the ON_COOLDOWN outcome.
        cooldown_until=(multi.outcomes[0].cooldown_until if multi.outcomes else None),
    )


@router.post("/check", response_model=BountyCheckResponse)
async def check_bounty(
    request: BountyCheckRequest,
    guild_id: int = Query(..., description="Discord guild ID"),
    service: BountyService = Depends(get_bounty_service),
):
    """Check a system against active bounties for a given guild.

    A single ``/check`` request may produce multiple outcomes — one per
    bounty in the player's division whose route contains the system
    (B.12 multi-bounty fix). The response returns ``outcomes[]`` plus
    backwards-compat top-level fields mirroring ``outcomes[0]``.
    """
    flogger.info(
        f"Bounty check request: player_id={request.player_id} system={request.system_name!r} guild_id={guild_id}"
    )
    try:
        async with get_db_session() as db:
            multi = await service.check_bounty(db, request.player_id, request.system_name, guild_id)
        flogger.info(
            f"Bounty check result: player_id={request.player_id}"
            f" system={request.system_name!r} result_count={len(multi.outcomes)}"
            f" results={[o.result.value for o in multi.outcomes]}"
            f" bounty_ids={[o.bounty_id for o in multi.outcomes]}"
        )
        return _build_check_response(multi)
    except HTTPException:
        raise
    except Exception as e:
        flogger.error(
            f"Bounty check failed: player_id={request.player_id}"
            f" system={request.system_name!r} guild_id={guild_id}: {e}"
        )
        # Return a graceful not-found response rather than propagating as a 500
        fallback = BountyCheckOutcome(
            result="not_found",
            bounty_id=None,
            message="No active bounties found or an error occurred processing the check.",
        )
        return BountyCheckResponse(
            outcomes=[fallback],
            result_count=1,
            result="not_found",
            bounty_id=None,
            message=fallback.message,
        )


# ---------------------------------------------------------------------------
# POST /bounties/combat-bonus  (Bronze division optional post-capture duel)
# ---------------------------------------------------------------------------


@router.post("/combat-bonus", response_model=CombatBonusResponse)
async def combat_bonus(
    request: CombatBonusRequest,
    service: BountyService = Depends(get_bounty_service),
):
    """Run optional post-capture combat for a Bronze-division player.

    Called when a Bronze player wants to attempt the 2x bonus duel after
    their bounty was auto-captured. The criminal ship data is provided by
    the caller (from the capture response's ``criminal_ship`` field).

    Win → awards ``base_reward`` additional credits (total becomes 2x).
    Lose → no penalty (player keeps the base reward already awarded).
    """
    from services.bounty_service import _serialize_fight_results
    from services.combat_service import CombatService
    from services.game_constants import GameConstants
    from services.loadout_builder import LoadoutBuilder

    flogger.info(f"Combat bonus request: player_id={request.player_id} base_reward={request.base_reward}")
    try:
        async with get_db_session() as db:
            # Build loadouts
            player_loadout = await LoadoutBuilder.from_player(db, request.player_id)
            criminal_loadout = LoadoutBuilder.from_criminal_ship(request.criminal_ship)

            # Run combat with PvC armour buff applied to the player (loadout1 = ship1).
            # PvP duels use the same CombatService.fight_ships() with default buff=1.0.
            combat_svc = CombatService()
            fight_results = combat_svc.fight_ships(
                player_loadout, criminal_loadout, player_armour_buff=GameConstants.BOUNTY_PVC_ARMOUR_BUFF_FACTOR
            )

            # Determine outcome (stalemate = player wins for bounties)
            won = fight_results.is_stalemate or (fight_results.winner_name == player_loadout.ship_name)
            bonus_credits = 0

            if won:
                # Award bonus credits directly to the player
                player = await service.player_repo.get_by_id(db, request.player_id)
                if player is not None:
                    player.credits += request.base_reward
                    player.lifetime_credits += request.base_reward
                    await db.commit()
                    bonus_credits = request.base_reward

            combat_dict = _serialize_fight_results(fight_results) or {}
            if won:
                msg = f"Combat victory! +{bonus_credits:,}cr bonus (2x total)!"
            else:
                loser = fight_results.loser_name or player_loadout.ship_name
                msg = f"Combat loss — {loser} was defeated. You keep the base reward."
            flogger.info(f"Combat bonus result: player_id={request.player_id} won={won} bonus_credits={bonus_credits}")
            return CombatBonusResponse(
                won=won,
                bonus_credits=bonus_credits,
                combat_result=combat_dict,
                message=msg,
            )
    except Exception as e:
        flogger.error(f"Combat bonus failed: player_id={request.player_id}: {e}")
        raise


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
        # B.24: compute 3-state system statuses server-side; mask "found" to prevent
        # answer leakage (client must not know which system the criminal is in)
        raw_statuses = _project_checked(bounty) or {}
        system_statuses = {k: ("checked" if v == "found" else v) for k, v in raw_statuses.items()}
        return {
            "bounty_id": bounty.id,
            "criminal_name": bounty.criminal_name,
            "division": bounty.division,
            "route": bounty.route,
            "checked": bounty.checked,
            "status": bounty.status,
            "system_statuses": system_statuses,
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


@router.get("/{bounty_id}/loadout", response_model=LoadoutResponse)
async def get_bounty_loadout(
    bounty_id: int,
    loadout_service: LoadoutResponseService = Depends(get_loadout_response_service),
) -> LoadoutResponse:
    """Get the criminal's ship loadout for a bounty.

    Returns a unified `LoadoutResponse` with `subject_kind="criminal"`, populated
    with Criminal.icon as the thumbnail and an effective cargo capacity derived
    from Ship.cargo × CompressorModule multipliers (spec §2.6, §7.11).
    """
    try:
        async with get_db_session() as db:
            response = await loadout_service.build_bounty_loadout(db, bounty_id)
            if response is None:
                raise HTTPException(status_code=404, detail="Bounty not found")
            return response
    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error getting bounty loadout {bounty_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get bounty loadout") from e


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
    """Admin-triggered bounty spawn — bypasses max-bounty cap.

    For each tier (or the specified tier):
    1. Load guild config for expiry settings.
    2. Spawn a bounty with the configured expiry (ignores active count / max cap).
    3. Schedule expiry job and post Discord announcement (best-effort, non-fatal).
    """
    from utils.executors.bounty_spawn_executor import _announce_bounty, _schedule_expiry_job

    flogger.info(f"Admin spawn bounties: guild_id={guild_id} tier={tier} user_id={user_id}")

    tiers_to_process = [tier] if tier else ["bronze", "silver", "gold", "platinum"]
    spawned_bounties: list[BountyResponse] = []
    skipped_tiers: list[str] = []
    errors: list[str] = []

    try:
        async with get_db_session() as db:
            # Load guild config
            config_repo = ConfigRepository()
            config = await config_repo.get_by_guild_id(db, guild_id)

            bounty_expiry_minutes: int = (
                config.bounty_expiry_minutes if config and config.bounty_expiry_minutes is not None else 480
            )

            for t in tiers_to_process:
                t_lower = t.lower()
                try:
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
