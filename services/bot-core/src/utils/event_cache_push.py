"""Non-fatal push of current event list to gateway autocomplete cache.

Mirrors bounty_spawn_executor._push_bounty_cache — same pattern, same non-fatal contract.
"""

from __future__ import annotations

import os
from urllib.parse import quote

import httpx
from shared.bblogger import get_logger

flogger = get_logger("event-cache-push")

_GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
_GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
_GATEWAY_BASE_URL = f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}/api/v1"


async def _push_events_cache(guild_id: int) -> None:
    """Non-fatal push of the current event list to the gateway autocomplete cache.

    Fetches all events for the guild via GET /events/guild/{guild_id} (self-call)
    and POSTs them to the gateway's internal autocomplete endpoint so the next
    events_autocomplete keystroke returns fresh data without a gateway→bot-core round-trip.

    Args:
        guild_id: The Discord guild ID to push events for.
    """
    try:
        from shared.http_retry import with_transient_retry  # deferred — avoids forkserver mock-shared collision

        # Self-call: read fresh event list from our own API.
        self_host = os.getenv("EXECUTOR_HOST", "bot-core")
        self_port = os.getenv("EXECUTOR_PORT", "8000")
        self_base = f"http://{self_host}:{self_port}/api/v1"

        safe_guild = int(guild_id)  # SSRF guard: must be numeric
        gateway_url = f"{_GATEWAY_BASE_URL}/internal/autocomplete/events-cache/{quote(str(safe_guild), safe='')}"
        token = os.getenv("INTERNAL_AUTH_TOKEN", "")
        headers = {"X-Internal-Auth": token} if token else {}

        async with httpx.AsyncClient() as client:
            events_resp = await with_transient_retry(
                client.get,
                f"{self_base}/events/guild/{safe_guild}",
                timeout=5.0,
            )
            events = events_resp.json() if events_resp.status_code == 200 else []

            await with_transient_retry(
                client.post,
                gateway_url,
                json={"events": events},
                headers=headers,
                timeout=5.0,
            )
        flogger.debug(f"_push_events_cache: pushed guild={guild_id} count={len(events)}")
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.warning(
            f"_push_events_cache: failed for guild={guild_id}: {type(e).__name__}: {e} — TTL refresh will reconcile"
        )
