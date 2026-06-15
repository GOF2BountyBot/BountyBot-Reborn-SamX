"""Shared bot-core → discord-gateway internal cache-push helpers.

Centralises the SSRF-guarded URL build + X-Internal-Auth + fire-and-forget
non-fatal semantics that several routers/executors previously duplicated.

All helpers are strictly non-fatal: any failure (network, 5xx, bad value) is
logged as WARNING and swallowed so a gateway-side problem can never break the
authoritative bot-core operation that triggered the push.
"""

from __future__ import annotations

import os
from urllib.parse import quote

import httpx
from shared import bblogger

flogger = bblogger.get_logger("bot-core-gateway-push")

_GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
_GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
_GATEWAY_BASE_URL = f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}/api/v1"
_INTERNAL_AUTH_TOKEN = os.getenv("INTERNAL_AUTH_TOKEN", "")


def _auth_headers() -> dict[str, str]:
    return {"x-internal-auth": _INTERNAL_AUTH_TOKEN} if _INTERNAL_AUTH_TOKEN else {}


async def push_combatlog_invalidate(guild_id: int, user_id: int) -> None:
    """Invalidate one user's combat-log autocomplete cache key on the gateway.

    Fire-and-forget; never raises. Called once per HUMAN combatant after a fight
    is logged (both PvC and PvP). NPC combatants (NULL user_id) are skipped by the
    caller and must never reach this function.
    """
    if user_id is None:
        return
    try:
        # SSRF guard: coerce to int so any non-numeric value raises before the URL is built.
        safe_guild = int(guild_id)
        safe_user = int(user_id)
        url = (
            f"{_GATEWAY_BASE_URL}/internal/autocomplete/combatlog-cache"
            f"/{quote(str(safe_guild), safe='')}/{quote(str(safe_user), safe='')}"
        )
        # Deferred import avoids forkserver mock-shared collision (matches duels.py).
        from shared.http_retry import with_transient_retry

        async with httpx.AsyncClient() as client:
            await with_transient_retry(client.post, url, headers=_auth_headers(), timeout=5)
        flogger.debug(f"push_combatlog_invalidate: invalidated guild={guild_id} user={user_id}")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(
            f"push_combatlog_invalidate: failed for guild={guild_id} user={user_id}: {type(exc).__name__}: {exc}"
        )


async def push_combatlog_invalidate_both(
    guild_id: int, combatant1_user_id: int | None, combatant2_user_id: int | None
) -> None:
    """Invalidate the combat-log cache for both combatants of a finished fight.

    Skips NULL user_ids (PvC criminal side has no Discord id — §12 NPC invariant).
    Strictly non-fatal: a push failure never propagates to the fight finalizer.
    """
    if combatant1_user_id is not None:
        await push_combatlog_invalidate(guild_id, combatant1_user_id)
    if combatant2_user_id is not None:
        await push_combatlog_invalidate(guild_id, combatant2_user_id)
