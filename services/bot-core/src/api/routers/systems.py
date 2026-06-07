"""
Systems API router — star system pathfinding and queries.
"""

from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from persist.database.manager import db_manager
from services.pathfinding_service import PathfindingError, PathfindingService
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

flogger = bblogger.get_logger("bot-systems-router")

router = APIRouter(prefix="/systems", tags=["systems"])

# Simple in-process cache keyed by (start, end) tuple.
_route_map_cache: dict[tuple[str, str], bytes] = {}


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with db_manager.get_session() as session:
        yield session


def _get_system_graph(request: Request):
    """Return the shared SystemGraphService from app.state (set at startup)."""
    graph = getattr(request.app.state, "system_graph", None)
    if graph is None:
        flogger.warning("system_graph not found on app.state — service may still be starting up")
        raise HTTPException(status_code=503, detail="System graph not yet available")
    return graph


def _get_map_renderer(request: Request):
    """Return the shared MapRenderer from app.state (set at startup)."""
    renderer = getattr(request.app.state, "map_renderer", None)
    if renderer is None:
        flogger.warning("map_renderer not found on app.state — service may still be starting up")
        raise HTTPException(status_code=503, detail="Map renderer not yet available")
    return renderer


@router.get("/route")
async def find_route(
    start: str,
    end: str,
    db: AsyncSession = Depends(get_db),
    graph_service=Depends(_get_system_graph),
) -> dict:
    """Find shortest route between two star systems using A* pathfinding.

    Returns the ordered list of system names from start to end,
    along with the total hop count.

    - **start**: Name of the starting star system
    - **end**: Name of the destination star system
    """
    flogger.info(f"Route query initiated: start='{start}' end='{end}'")
    try:
        if not graph_service.is_loaded():
            flogger.debug("System graph not yet loaded, loading from database")
            await graph_service.load_graph(db)
            flogger.debug("System graph loaded successfully")

        pf_service = PathfindingService(graph_service)
        result = pf_service.make_route(start, end)
    except Exception as e:
        flogger.error(f"Pathfinding service error: start='{start}' end='{end}' error={type(e).__name__}: {e}")
        raise

    if isinstance(result, PathfindingError):
        if result == PathfindingError.NO_ROUTE_FOUND:
            flogger.info(f"No route found: start='{start}' end='{end}'")
            raise HTTPException(
                status_code=404,
                detail=f"No route found between '{start}' and '{end}'",
            )
        if result == PathfindingError.MAX_LENGTH_REACHED:
            flogger.warning(f"Route exceeds max length: start='{start}' end='{end}'")
            raise HTTPException(
                status_code=400,
                detail="Route exceeds maximum length (50 hops)",
            )
        # Generic fallback for any future error values
        flogger.error(f"Unknown pathfinding error: {result}")
        raise HTTPException(status_code=400, detail=str(result))

    hop_count = len(result) - 1
    flogger.info(f"Route found: start='{start}' end='{end}' hops={hop_count} path={result}")
    flogger.debug(f"Route details: full_path={result}")
    return {"route": result, "hops": hop_count}


@router.get("/route/map", response_class=Response)
async def get_route_map(
    start: str,
    end: str,
    db: AsyncSession = Depends(get_db),
    graph_service=Depends(_get_system_graph),
    map_renderer=Depends(_get_map_renderer),
) -> Response:
    """Return a PNG star map image with the route between two systems overlaid.

    Computes the A* route between *start* and *end*, renders it using
    MapRenderer and returns the image as ``image/png``.  Results are cached
    in-process by ``(start, end)`` so repeated requests are cheap.

    - **start**: Name of the starting star system
    - **end**: Name of the destination star system
    """
    flogger.info(f"Route map requested: start='{start}' end='{end}'")

    cache_key = (start, end)
    if cache_key in _route_map_cache:
        flogger.debug(f"Route map cache hit: start='{start}' end='{end}'")
        return Response(content=_route_map_cache[cache_key], media_type="image/png")

    flogger.debug(f"Route map cache miss: start='{start}' end='{end}', computing route")

    # Re-use the same pathfinding logic as find_route.
    try:
        if not graph_service.is_loaded():
            flogger.debug("System graph not yet loaded, loading from database")
            await graph_service.load_graph(db)

        pf_service = PathfindingService(graph_service)
        result = pf_service.make_route(start, end)
    except Exception as e:
        flogger.error(f"Pathfinding error during map render: start='{start}' end='{end}' error={type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Failed to compute route for map") from e

    if isinstance(result, PathfindingError):
        if result == PathfindingError.NO_ROUTE_FOUND:
            flogger.info(f"No route found for map: start='{start}' end='{end}'")
            raise HTTPException(
                status_code=404,
                detail=f"No route found between '{start}' and '{end}'",
            )
        if result == PathfindingError.MAX_LENGTH_REACHED:
            flogger.warning(f"Route exceeds max length for map: start='{start}' end='{end}'")
            raise HTTPException(
                status_code=400,
                detail="Route exceeds maximum length (50 hops)",
            )
        flogger.error(f"Unknown pathfinding error during map render: {result}")
        raise HTTPException(status_code=400, detail=str(result))

    route: list[str] = result
    try:
        png_bytes = map_renderer.render_route_for_bounty(route, graph_service)
        _route_map_cache[cache_key] = png_bytes
        flogger.info(f"Route map rendered: start='{start}' end='{end}' systems={len(route)}")
    except Exception as e:
        flogger.error(f"Map render failed: start='{start}' end='{end}' error={type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Failed to render route map") from e

    return Response(content=_route_map_cache[cache_key], media_type="image/png")
