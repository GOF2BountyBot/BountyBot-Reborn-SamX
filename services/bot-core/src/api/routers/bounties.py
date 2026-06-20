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

import asyncio
import contextlib
import os
from collections import OrderedDict

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from persist.database.manager import db_manager, get_db_session
from persist.repositories.config_repository import ConfigRepository
from services.audit_service import AuditService
from services.bounty_service import BountyService
from services.loadout_response_service import LoadoutResponseService
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
    LootResult,
)
from api.schemas.loadout_schema import LoadoutResponse

flogger = bblogger.get_logger("bounty-router")

# ---------------------------------------------------------------------------
# Bounded LRU in-process cache: (bounty_id, route_tuple) -> PNG bytes
#
# Memory math: each PNG is ~1.9 MB.  Cap of 32 ≈ 60 MB worst case — large
# enough to cover typical multi-guild deployments (4 divisions × ~8 guilds)
# while preventing unbounded growth from accumulated unique bounty keys.
#
# Implementation: OrderedDict with move-to-end on access (LRU) and
# popitem(last=False) on overflow (evict oldest).  ALL mutations (write,
# move-to-end, evict) happen on the event-loop thread ONLY — never from
# inside an offloaded render worker.
# ---------------------------------------------------------------------------
_MAP_CACHE_MAX = 32
_map_cache: OrderedDict[tuple[int, tuple[str, ...]], bytes] = OrderedDict()


def _map_cache_get(key: tuple[int, tuple[str, ...]], default: bytes | None = None) -> bytes | None:
    """Loop-thread LRU read: return value and move the entry to MRU position."""
    if key not in _map_cache:
        return default
    _map_cache.move_to_end(key)
    return _map_cache[key]


def _map_cache_set(key: tuple[int, tuple[str, ...]], value: bytes) -> None:
    """Loop-thread LRU write: insert/update and evict LRU entry on overflow."""
    if key in _map_cache:
        _map_cache.move_to_end(key)
    _map_cache[key] = value
    if len(_map_cache) > _MAP_CACHE_MAX:
        _map_cache.popitem(last=False)


def get_bounty_service() -> BountyService:
    return BountyService()


def get_loadout_response_service() -> LoadoutResponseService:
    return LoadoutResponseService()


def _get_map_renderer(request: Request):
    """Return the shared MapRenderer from app.state (set at startup).

    Raises HTTP 503 when the renderer is absent so the endpoint fails fast
    rather than returning a misleading result.
    """
    renderer = getattr(request.app.state, "map_renderer", None)
    if renderer is None:
        flogger.warning("map_renderer not found on app.state — service may still be starting up")
        raise HTTPException(status_code=503, detail="Map renderer not yet available")
    return renderer


def _get_system_graph(request: Request):
    """Return the shared SystemGraphService from app.state (set at startup).

    Raises HTTP 503 when the graph is absent so the endpoint fails fast
    rather than returning a misleading result.
    """
    graph = getattr(request.app.state, "system_graph", None)
    if graph is None:
        flogger.warning("system_graph not found on app.state — service may still be starting up")
        raise HTTPException(status_code=503, detail="System graph not yet available")
    return graph


def _get_map_renderer_optional(request: Request):
    """Return the shared MapRenderer from app.state, or None if absent.

    Used by multi-phase write endpoints (e.g. admin-spawn) where map rendering
    is best-effort: the primary operation (spawn + audit log) must succeed
    regardless of renderer availability.  Hard-failing Depends would abort the
    entire endpoint on a renderer outage — that is not acceptable for writes.
    """
    return getattr(request.app.state, "map_renderer", None)


def _get_system_graph_optional(request: Request):
    """Return the shared SystemGraphService from app.state, or None if absent.

    Used by multi-phase write endpoints (e.g. admin-spawn) where graph access
    is best-effort: the primary operation must succeed even when the graph is
    temporarily unavailable (e.g. during startup).
    """
    return getattr(request.app.state, "system_graph", None)


router = APIRouter(
    prefix="/bounties",
    tags=["bounties"],
    responses={404: {"description": "Bounty not found"}},
)


# ---------------------------------------------------------------------------
# POST /bounties/check
# ---------------------------------------------------------------------------


def _loot_to_schema(loot) -> LootResult | None:
    """Map an internal :class:`~services.bounty_service.LootOutcome` to a wire
    :class:`LootResult`, applying the §5.9 omission rule.

    Returns ``None`` (the gateway omits the Loot field entirely) whenever there
    is no loot to render — ``loot is None`` (no combat win / no loot write) OR
    ``loot.outcome == "none"`` (no tractor beam equipped / nothing looted).  Only
    the four renderable states (``looted``/``partial``/``failed``/``cargo_full``)
    yield a :class:`LootResult`.  ``item_type`` is intentionally NOT surfaced —
    the gateway never needs it (§5.9).
    """
    if loot is None or loot.outcome == "none":
        return None
    return LootResult(
        outcome=loot.outcome,
        item_name=loot.item_name,
        qty_looted=loot.qty_looted,
        qty_total=loot.qty_total,
        tractor_emoji=loot.tractor_emoji,
        cargo_current=loot.cargo_current,
        cargo_max=loot.cargo_max,
    )


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
        reward_per_sys=outcome.reward_per_sys,
        route_length=outcome.route_length,
        payout_breakdown=outcome.payout_breakdown if outcome.payout_breakdown else None,
        recently_spotted=outcome.recently_spotted,
        proximity_hint=outcome.proximity_hint,
        distance_to_answer=outcome.distance_to_answer,
        loot=_loot_to_schema(outcome.loot),
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
        # T6: legacy single-bounty mirror of outcomes[0].loot (None if first has none).
        loot=first.loot if first else None,
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
            multi = await service.check_bounty(db, request.player_id, request.system_name, guild_id)  # noqa: TRANSACTION_DISCIPLINE — fight_ships owns its commit via CombatLogService.persist
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
    from services.game_constants import GameConstants, resolve_constant
    from services.loadout_builder import LoadoutBuilder

    flogger.info(f"Combat bonus request: player_id={request.player_id} base_reward={request.base_reward}")
    try:
        async with get_db_session() as db:
            # Fetch player early — raise 404 immediately if not found
            player = await service.player_repo.get_by_id(db, request.player_id)
            if player is None:
                flogger.info(f"combat_bonus: player_id={request.player_id} not found — returning 404")
                raise HTTPException(status_code=404, detail=f"Player {request.player_id} not found")

            # Load per-guild config for PvC DR override (T10: pvc_damage_reduction replaces pvc_armour_buff_factor)
            guild_cfg = None
            if hasattr(player, "guild_id") and player.guild_id:
                guild_cfg = await ConfigRepository().get_by_guild_id(db, player.guild_id)
            _pvc_dr = resolve_constant(guild_cfg, "pvc_damage_reduction", GameConstants.PVC_DAMAGE_REDUCTION)

            # Build loadouts
            player_loadout = await LoadoutBuilder.from_player(db, request.player_id)
            criminal_loadout = LoadoutBuilder.from_criminal_ship(request.criminal_ship)

            # CI-20: resolve display labels for combat-log thread naming
            from services.bounty_service import _resolve_combat_label

            _player_label = await _resolve_combat_label(db, player)
            _criminal_label = request.criminal_ship.get("criminal_name") or criminal_loadout.ship_name

            # Run combat via TickResolver (T10: async, persists combat_log, increments Player stats)
            combat_svc = CombatService()
            fight_results = await combat_svc.fight_ships(  # noqa: TRANSACTION_DISCIPLINE — fight_ships owns its commit via CombatLogService.persist
                player_loadout,
                criminal_loadout,
                context="bounty_bonus",
                log_result=True,
                pvc_damage_reduction=_pvc_dr,
                session=db,
                guild_id=player.guild_id,
                combatant1_user_id=player.user_id,
                combatant2_user_id=None,  # NPC side
                combatant1_label=_player_label,
                combatant2_label=_criminal_label,
            )

            # P2-T8b: player is always combatant1 (loadout1 / side-1).
            # winner_side==1 → player won; winner_side==2 → criminal won.
            # Stalemate counts as a loss — no 2× bonus (spec §9 PvC draw semantics).
            won = fight_results.winner_side == 1
            bonus_credits = 0

            if won:
                await service._award_combat_bonus(db, request.player_id, request.base_reward)
                bonus_credits = request.base_reward

            combat_dict = _serialize_fight_results(fight_results) or {}
            if won:
                msg = f"Combat victory! +{bonus_credits:,}cr bonus (2x total)!"
            elif fight_results.is_stalemate:
                msg = "Stalemate — no bonus. You keep the base reward."
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
    except HTTPException:
        raise
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
    map_renderer=Depends(_get_map_renderer),
    system_graph=Depends(_get_system_graph),
):
    """Return a PNG image of the star map with the bounty route overlaid."""
    async with get_db_session() as db:
        bounty = await service.bounty_repo.get_by_id(db, bounty_id)
        if bounty is None:
            raise HTTPException(status_code=404, detail="Bounty not found")

        route: list[str] = list(bounty.route) if bounty.route else []
        cache_key = (bounty_id, tuple(route))

        cached = _map_cache_get(cache_key)
        if cached is not None:
            flogger.debug(f"Map cache hit for bounty_id={bounty_id}")
        else:
            flogger.debug(f"Map cache miss for bounty_id={bounty_id}, rendering")
            # Ensure system graph is populated (should already be at startup).
            if not system_graph.is_loaded():
                await system_graph.load_graph(db)

            try:
                png_bytes = await map_renderer.render_route_offloaded(route, system_graph)
                _map_cache_set(cache_key, png_bytes)  # loop-thread write; safe
                cached = png_bytes
                flogger.info(f"Map rendered for bounty_id={bounty_id} route={len(route)} systems")
            except Exception as e:
                flogger.error(f"Map render failed for bounty_id={bounty_id}: {e}")
                raise

        return Response(content=cached, media_type="image/png")


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

            response = ClearBountiesResponse(
                guild_id=result["guild_id"],
                tier=result["tier"],
                cleared_count=result["cleared_count"],
                bounty_ids=result["bounty_ids"],
                announcements_deleted=result["announcements_deleted"],
            )

        # Push empty/updated bounty list to gateway cache (best-effort, non-fatal).
        # Opens a fresh session so the cleared state is visible to the read.
        if result["cleared_count"] > 0:
            try:
                from utils.executors.bounty_spawn_executor import _push_bounty_cache

                async with db_manager.get_session() as push_db:
                    await _push_bounty_cache(f"admin-clear-{guild_id}", guild_id, push_db)
            except Exception as push_exc:  # pylint: disable=broad-exception-caught
                flogger.warning(f"Clear bounties: non-fatal cache push failure for guild={guild_id}: {push_exc}")

        return response
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
    tier: str | None = Query(None, description="Division tier to spawn: bronze, silver, gold, or platinum"),
    user_id: int = Query(..., description="Admin Discord user ID for audit log"),
    quantity: int = Query(1, ge=1, le=10, description="Number of bounties to spawn per tier (1-10)"),
    service: BountyService = Depends(get_bounty_service),
    _map_renderer=Depends(_get_map_renderer_optional),
    _system_graph=Depends(_get_system_graph_optional),
):
    """Admin-triggered bounty spawn — bypasses max-bounty cap.

    For each tier (or the specified tier), spawns `quantity` bounties:
    1. Load guild config for expiry settings.
    2. Spawn bounties with the configured expiry (ignores active count / max cap).
    3. Schedule expiry jobs and post Discord announcements (best-effort, non-fatal).
    """
    from utils.executors.bounty_spawn_executor import (
        _announce_bounty,
        _push_bounty_cache,
        _schedule_expiry_job,
    )

    flogger.info(f"Admin spawn bounties: guild_id={guild_id} tier={tier} quantity={quantity} user_id={user_id}")

    tiers_to_process = [tier] if tier else ["bronze", "silver", "gold", "platinum"]
    spawned_bounties: list[BountyResponse] = []
    spawned_orm: list = []  # raw ORM objects for post-actions
    skipped_tiers: list[str] = []
    errors: list[str] = []
    config = None

    try:
        # ----------------------------------------------------------------
        # Phase 1 — sequential DB writes (single session, no concurrency)
        # All spawn_bounty() calls share one AsyncSession and must be serial.
        # ----------------------------------------------------------------
        async with get_db_session() as db:
            config_repo = ConfigRepository()
            config = await config_repo.get_by_guild_id(db, guild_id)

            bounty_expiry_minutes: int = (
                config.bounty_expiry_minutes if config and config.bounty_expiry_minutes is not None else 480
            )

            for t in tiers_to_process:
                t_lower = t.lower()
                for _ in range(quantity):
                    try:
                        bounty = await service.spawn_bounty(db, guild_id, t_lower, expiry_minutes=bounty_expiry_minutes)
                        if bounty is None:
                            errors.append(f"Failed to spawn bounty for tier={t_lower}: no criminals or route available")
                        else:
                            spawned_bounties.append(BountyResponse.model_validate(bounty))
                            spawned_orm.append(bounty)
                            flogger.info(f"Admin spawned bounty {bounty.id} for guild={guild_id} tier={t_lower}")
                    except Exception as e:  # pylint: disable=broad-exception-caught
                        flogger.error(f"Admin spawn error for guild={guild_id} tier={t_lower}: {e}")
                        errors.append(f"Error spawning tier={t_lower}: {e}")

            await AuditService.log_action(
                db,
                user_id=user_id,
                action="admin_spawn_bounties",
                guild_id=guild_id,
                resource_type="bounty",
                resource_id=str(guild_id),
                details={
                    "tier": tier,
                    "quantity": quantity,
                    "spawned_count": len(spawned_bounties),
                    "skipped_tiers": skipped_tiers,
                    "errors": errors,
                },
            )

        # ----------------------------------------------------------------
        # Phase 2a — parallel fan-out render for all route maps.
        #
        # For each spawned bounty, resolve its route list on the loop (cheap,
        # read-only graph access), then fan out ALL PIL renders concurrently
        # via the T3 offload seam (render_route_offloaded).  Coord resolution
        # happens inside render_route_offloaded Phase-1 (on the loop thread);
        # the pure PIL work runs concurrently on the thread pool.
        #
        # CACHE WRITE DISCIPLINE: _map_cache is written ON THE LOOP THREAD
        # ONLY — after gather() resolves, in the loop body below.  Worker
        # threads must never write to this dict (no thread-safety guarantee).
        # Bounded-LRU eviction (_MAP_CACHE_MAX=32) is handled by _map_cache_set.
        #
        # Dead-check removed: newly-spawned bounties cannot already be in
        # _map_cache (their IDs are fresh from Phase 1), so the old
        # ``if cache_key in _map_cache`` branch was always-missing dead code.
        #
        # _map_renderer / _system_graph come from Depends (optional — None when
        # the renderer/graph is not yet available at startup).
        # ----------------------------------------------------------------
        bounty_pngs: dict[int, bytes] = {}  # bounty_id -> PNG bytes
        if spawned_orm and _map_renderer is not None and _system_graph is not None:
            try:
                async with get_db_session() as render_db:
                    if not _system_graph.is_loaded():
                        await _system_graph.load_graph(render_db)

                # Build one render coroutine per bounty (coords resolved on loop inside
                # render_route_offloaded Phase-1; PIL work offloaded to thread pool).
                async def _render_one(b) -> tuple[int, list[str], bytes]:
                    route = list(b.route) if b.route else []
                    try:
                        png = await _map_renderer.render_route_offloaded(route, _system_graph)
                    except Exception as render_exc:  # pylint: disable=broad-exception-caught
                        flogger.warning(
                            f"Admin spawn: map render failed for bounty {b.id}: {render_exc} — "
                            "will announce without route map image"
                        )
                        return (b.id, route, b"")
                    return (b.id, route, png)

                render_results = await asyncio.gather(*[_render_one(b) for b in spawned_orm])

                # Write cache ON THE LOOP THREAD ONLY — after gather resolves.
                for bounty_id, route, png in render_results:
                    if png:
                        cache_key = (bounty_id, tuple(route))
                        _map_cache_set(cache_key, png)  # loop-thread write; bounded LRU
                        bounty_pngs[bounty_id] = png

            except Exception as graph_exc:  # pylint: disable=broad-exception-caught
                flogger.warning(f"Admin spawn: system graph load failed: {graph_exc} — skipping all map images")
        elif spawned_orm:
            flogger.warning("Admin spawn: map_renderer or system_graph not on app.state — skipping map rendering")

        # ----------------------------------------------------------------
        # Phase 2b — batch-upload route maps to gateway image channel.
        # Discord allows up to 10 attachments per message; one batched POST
        # consumes ONE per-channel rate-limit slot. This turns N serial
        # uploads into ceil(N/10) batched calls (e.g. 20 → 2 calls).
        # ----------------------------------------------------------------
        route_map_urls: dict[int, str] = {}  # bounty_id -> Discord CDN URL
        image_channel_id = getattr(config, "image_channel_id", None) if config else None
        if bounty_pngs and image_channel_id is not None:
            gateway_host = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
            gateway_port = os.getenv("GATEWAY_PORT", "7999")
            gateway_base = f"http://{gateway_host}:{gateway_port}/api/v1"
            batch_url = f"{gateway_base}/channels/{image_channel_id}/upload-batch"

            bounty_ids = list(bounty_pngs.keys())
            batches = [bounty_ids[i : i + 10] for i in range(0, len(bounty_ids), 10)]

            async with httpx.AsyncClient(timeout=60) as client:
                for batch in batches:
                    files = [("files", (f"route_map_{bid}.png", bounty_pngs[bid], "image/png")) for bid in batch]
                    try:
                        resp = await client.post(batch_url, files=files)
                        resp.raise_for_status()
                        payload = resp.json()
                        for item in payload.get("data", []):
                            fname = item.get("filename", "")
                            # Reverse: "route_map_{bid}.png" -> bid
                            if fname.startswith("route_map_") and fname.endswith(".png"):
                                with contextlib.suppress(ValueError, KeyError):
                                    bid = int(fname[len("route_map_") : -len(".png")])
                                    route_map_urls[bid] = item["attachment_url"]
                    except Exception as up_exc:  # pylint: disable=broad-exception-caught
                        flogger.warning(
                            f"Admin spawn: batch upload failed for {len(batch)} maps: "
                            f"{type(up_exc).__name__}: {up_exc} — announcing without images"
                        )
            flogger.info(
                f"Admin spawn: batch-uploaded {len(route_map_urls)}/{len(bounty_pngs)} route maps "
                f"in {len(batches)} batch(es) for guild={guild_id}"
            )

        # ----------------------------------------------------------------
        # Phase 2c — parallel post-actions per bounty.
        # Each task: schedule expiry job + announce (with pre-resolved
        # route_map_url so no per-bounty upload happens). The semaphore is
        # kept as a guardrail against any future fall-back to per-bounty
        # uploads inside _announce_bounty.
        # ----------------------------------------------------------------
        _announce_sem = asyncio.Semaphore(4)

        async def _post_actions(bounty) -> None:
            job_id = f"admin-spawn-{guild_id}"
            try:
                await _schedule_expiry_job(job_id, bounty)
            except Exception as sched_exc:  # pylint: disable=broad-exception-caught
                flogger.warning(f"Admin spawn: non-fatal expiry scheduling failure for bounty {bounty.id}: {sched_exc}")
            try:
                async with _announce_sem, db_manager.get_session() as ann_db:
                    await _announce_bounty(
                        job_id,
                        bounty,
                        config,
                        ann_db,
                        pre_resolved_route_map_url=route_map_urls.get(bounty.id),
                    )
            except Exception as ann_exc:  # pylint: disable=broad-exception-caught
                flogger.warning(f"Admin spawn: non-fatal announcement failure for bounty {bounty.id}: {ann_exc}")

        if spawned_orm:
            results = await asyncio.gather(*[_post_actions(b) for b in spawned_orm], return_exceptions=True)
            for i, res in enumerate(results):
                if isinstance(res, Exception):
                    flogger.warning(f"Admin spawn: post-action task {i} raised: {res}")

            # Push gateway bounty cache once after all spawns (single fresh session)
            try:
                async with db_manager.get_session() as push_db:
                    await _push_bounty_cache(f"admin-spawn-{guild_id}", guild_id, push_db)
            except Exception as push_exc:  # pylint: disable=broad-exception-caught
                flogger.warning(f"Admin spawn: non-fatal cache push failure for guild={guild_id}: {push_exc}")

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
