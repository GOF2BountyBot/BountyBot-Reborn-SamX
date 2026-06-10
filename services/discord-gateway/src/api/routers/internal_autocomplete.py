"""Internal push endpoints for bot-core → gateway cache updates.

Protected by X-Internal-Auth shared-secret header.

These endpoints allow bot-core executors to push fresh shop stock and bounty
data directly into the gateway cog caches, eliminating the need for the
gateway to poll bot-core on every autocomplete keystroke.
"""

import logging
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from shared import bblogger

from api.schemas.internal_schemas import BountyCachePush, DuelCachePush, ShopCachePush

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
        logging.getLogger(__name__).warning("INTERNAL_AUTH_TOKEN not set — push endpoint is unauthenticated")
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
    flogger.info(f"push_shop_cache: updated cache for guild={guild_id} tier={tier} items={len(payload.items)}")
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
            f"push_bounty_cache: BountyCog not loaded or _bounty_cache not present (guild={guild_id}) — graceful no-op"
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    cog._bounty_cache.set(guild_id, payload.bounties)
    flogger.info(f"push_bounty_cache: updated cache for guild={guild_id} bounties={len(payload.bounties)}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Duel cache push
# ---------------------------------------------------------------------------


@router.post(
    "/duel-cache/{guild_id}/{player_id}",
    status_code=204,
    summary="Push duel lists into gateway DuelCog autocomplete caches",
    description=(
        "Called by bot-core duels router / duel_expire_executor after each challenge creation, "
        "accept, reject, cancel, or expiry. Writes the updated pending/outgoing duel lists directly "
        "into DuelCog._pending_duel_cache and DuelCog._outgoing_duel_cache so the next autocomplete "
        "keystroke is served from cache without a GET call to bot-core."
    ),
)
async def push_duel_cache(
    request: Request,
    guild_id: int,
    player_id: int,
    payload: DuelCachePush,
) -> Response:
    """Update the gateway DuelCog autocomplete caches for one guild/player."""
    await _verify_auth(request.headers.get("x-internal-auth"))

    bot = getattr(request.app.state, "bot", None)
    if bot is None:
        flogger.warning("push_duel_cache: app.state.bot not available")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bot not initialised")

    cog = bot.get_cog("DuelCog")
    if cog is None:
        flogger.warning(f"push_duel_cache: DuelCog not loaded (guild={guild_id} player={player_id})")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="DuelCog not loaded")

    cog._pending_duel_cache.set((guild_id, player_id), payload.pending_duels)
    cog._outgoing_duel_cache.set((guild_id, player_id), payload.outgoing_duels)
    flogger.info(
        f"push_duel_cache: updated cache for guild={guild_id} player={player_id} "
        f"pending={len(payload.pending_duels)} outgoing={len(payload.outgoing_duels)}"
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Combat-log cache invalidate (per-user)
# ---------------------------------------------------------------------------


@router.post(
    "/combatlog-cache/{guild_id}/{user_id}",
    status_code=204,
    summary="Invalidate one user's combat-log autocomplete cache key",
    description=(
        "Called by bot-core's combat finalizer (CombatLogService.persist) once per "
        "HUMAN combatant after a fight is logged, for BOTH PvC (/check) and PvP "
        "(/duel-accept). Drops the per-user key so the next /combat-log autocomplete "
        "keystroke cold-fills the freshly-written history. Invalidate-only (no "
        "payload): cheaper than a full-list push for this high-cardinality per-user "
        "cache, and the 120s TTL is a dead-man switch if a push is ever missed. "
        "PvC criminal combatants have no Discord id and are never pushed (the caller "
        "skips NULL user_ids). Gracefully no-ops when CombatLogCog is absent."
    ),
)
async def invalidate_combatlog_cache(
    request: Request,
    guild_id: int,
    user_id: int,
) -> Response:
    """Invalidate the per-user combat-log autocomplete cache for one (guild, user)."""
    await _verify_auth(request.headers.get("x-internal-auth"))

    bot = getattr(request.app.state, "bot", None)
    if bot is None:
        flogger.warning("invalidate_combatlog_cache: app.state.bot not available")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bot not initialised")

    cog = bot.get_cog("CombatLogCog")
    if cog is None or not hasattr(cog, "_combatlog_cache"):
        # Graceful no-op so a transient cog-load gap never 500s the fight finalizer.
        flogger.warning(
            f"invalidate_combatlog_cache: CombatLogCog not loaded or _combatlog_cache absent "
            f"(guild={guild_id} user={user_id}) — graceful no-op"
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    cog._combatlog_cache.invalidate((guild_id, user_id))
    flogger.info(f"invalidate_combatlog_cache: dropped key for guild={guild_id} user={user_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Autocomplete cache health check
# ---------------------------------------------------------------------------


@router.get(
    "/health",
    summary="Autocomplete cache health for ops verification",
    description=(
        "Returns the current size of shared autocomplete caches. "
        "Useful for ops to verify caches are populated after bot startup."
    ),
)
async def autocomplete_cache_health(
    request: Request,
    x_internal_auth: str | None = Header(None),
) -> dict[str, Any]:
    """Returns cache sizes and initialization state for ops verification."""
    await _verify_auth(x_internal_auth)

    import utils.autocomplete_state as state

    return {
        "player_cache_size": state.player_cache.size if state.player_cache else 0,
        "inventory_cache_size": state.inventory_cache.size if state.inventory_cache else 0,
        "ships_cache_size": state.ships_cache.size if state.ships_cache else 0,
        "initialized": state._initialized,
    }
