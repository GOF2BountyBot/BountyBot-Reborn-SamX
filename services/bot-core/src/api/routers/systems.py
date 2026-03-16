"""
Systems API router — star system pathfinding and queries.
"""

from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from persist.database.manager import db_manager
from services.pathfinding_service import PathfindingError, PathfindingService
from services.system_graph_service import SystemGraphService
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

flogger = bblogger.get_logger("bot-systems-router")

router = APIRouter(prefix="/systems", tags=["systems"])

# Module-level singleton — graph is loaded once and cached across requests.
_graph_service = SystemGraphService()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with db_manager.get_session() as session:
        yield session


@router.get("/route")
async def find_route(
    start: str,
    end: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Find shortest route between two star systems using A* pathfinding.

    Returns the ordered list of system names from start to end,
    along with the total hop count.

    - **start**: Name of the starting star system
    - **end**: Name of the destination star system
    """
    flogger.debug(f"Route query: start={start!r} end={end!r}")
    try:
        if not _graph_service.is_loaded():
            await _graph_service.load_graph(db)

        pf_service = PathfindingService(_graph_service)
        result = pf_service.make_route(start, end)
    except Exception as e:
        flogger.error(f"Route query error: start={start!r} end={end!r}: {e}")
        raise

    if isinstance(result, PathfindingError):
        if result == PathfindingError.NO_ROUTE_FOUND:
            flogger.debug(f"Route not found: start={start!r} end={end!r}")
            raise HTTPException(
                status_code=404,
                detail=f"No route found between '{start}' and '{end}'",
            )
        if result == PathfindingError.MAX_LENGTH_REACHED:
            flogger.debug(f"Route exceeds max length: start={start!r} end={end!r}")
            raise HTTPException(
                status_code=400,
                detail="Route exceeds maximum length (50 hops)",
            )
        # Generic fallback for any future error values
        raise HTTPException(status_code=400, detail=str(result))

    flogger.debug(f"Route found: {start!r} → {end!r} hops={len(result) - 1}")
    return {"route": result, "hops": len(result) - 1}
