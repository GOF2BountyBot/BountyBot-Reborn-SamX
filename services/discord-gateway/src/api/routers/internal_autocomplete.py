"""Internal push endpoints for bot-core → gateway cache updates.

Protected by X-Internal-Auth shared-secret header.

These endpoints allow bot-core executors to push fresh shop stock and bounty
data directly into the gateway cog caches, eliminating the need for the
gateway to poll bot-core on every autocomplete keystroke.
"""

import logging
import os

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from shared import bblogger

from api.schemas.internal_schemas import BountyCachePush, ShopCachePush

flogger = bblogger.get_logger("gateway-internal-autocomplete")

router = APIRouter(
    prefix="/internal/autocomplete",
    tags=["internal-autocomplete"],
    responses={
        401: {"description": "Invalid or missing internal auth token"},
        503: {"description": "Bot or cog not ready"},
    },
)


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


async def _verify_auth(x_internal_auth: str | None = Header(None)) -> None:
    """Verify X-Internal-Auth shared-secret header.

    In dev mode (INTERNAL_AUTH_TOKEN not set), logs a warning and allows the
    request through.  In production, rejects requests with wrong or missing token.
    """
    token = os.getenv("INTERNAL_AUTH_TOKEN", "")
    if not token:
        logging.getLogger(__name__).warning(
            "INTERNAL_AUTH_TOKEN not set — push endpoint is unauthenticated"
        )
        return
    if x_internal_auth != token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal auth token")


# ---------------------------------------------------------------------------
# Shop cache push
# ---------------------------------------------------------------------------


@router.post(
    "/shop-cache/{guild_id}/{tier}",
    status_code=204,
    summary="Push shop stock into gateway ShopCog cache",
    description=(
        "Called by bot-core shop_refresh_executor after each tier refresh. "
        "Writes the new stock list directly into ShopCog._shop_cache so the "
        "next autocomplete keystroke returns the refreshed inventory without "
        "a GET call to bot-core."
    ),
)
async def push_shop_cache(
    request: Request,
    guild_id: int,
    tier: str,
    payload: ShopCachePush,
    _auth: None = None,  # populated by dependency below
) -> Response:
    """Update the gateway ShopCog autocomplete cache for one guild/tier."""
    # Re-validate auth inline (dependency injection via Header is handled in the decorator)
    await _verify_auth(request.headers.get("x-internal-auth"))

    bot = getattr(request.app.state, "bot", None)
    if bot is None:
        flogger.warning("push_shop_cache: app.state.bot not available")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bot not initialised")

    cog = bot.get_cog("ShopCog")
    if cog is None:
        flogger.warning(f"push_shop_cache: ShopCog not loaded (guild={guild_id} tier={tier})")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="ShopCog not loaded")

    cog._shop_cache.set((guild_id, tier), payload.items)
    flogger.info(
        f"push_shop_cache: updated cache for guild={guild_id} tier={tier} items={len(payload.items)}"
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Bounty cache push
# ---------------------------------------------------------------------------


@router.post(
    "/bounty-cache/{guild_id}",
    status_code=204,
    summary="Push active bounty list into gateway BountyCog cache",
    description=(
        "Called by bot-core bounty_spawn_executor / bounty_expire_executor after "
        "each spawn or expiry.  Gracefully no-ops if BountyCog does not yet "
        "have a _bounty_cache attribute (Phase 6 adds it)."
    ),
)
async def push_bounty_cache(
    request: Request,
    guild_id: int,
    payload: BountyCachePush,
) -> Response:
    """Update the gateway BountyCog autocomplete cache for one guild."""
    await _verify_auth(request.headers.get("x-internal-auth"))

    bot = getattr(request.app.state, "bot", None)
    if bot is None:
        flogger.warning("push_bounty_cache: app.state.bot not available")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bot not initialised")

    cog = bot.get_cog("BountyCog")
    if cog is None or not hasattr(cog, "_bounty_cache"):
        # Phase 6 adds _bounty_cache.  Until then, silently accept the push.
        flogger.warning(
            f"push_bounty_cache: BountyCog not loaded or _bounty_cache not present "
            f"(guild={guild_id}) — graceful no-op"
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    cog._bounty_cache.set(guild_id, payload.bounties)
    flogger.info(
        f"push_bounty_cache: updated cache for guild={guild_id} bounties={len(payload.bounties)}"
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
