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
from services.bounty_service import BountyService
from services.map_renderer import MapRenderer
from services.system_graph_service import SystemGraphService
from shared import bblogger

from api.schemas.bounty_schema import (
    BountyCheckRequest,
    BountyCheckResponse,
    BountyCreateRequest,
    BountyPublicResponse,
    BountyResponse,
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
        )
    except Exception as e:
        flogger.error(
            f"Bounty check failed: player_id={request.player_id}"
            f" system={request.system_name!r} guild_id={guild_id}: {e}"
        )
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
