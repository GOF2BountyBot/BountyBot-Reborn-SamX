"""Shared shop-refresh announcement helper.

Extracted from ``utils.executors.shop_refresh_executor`` so that both the
scheduled executor AND the admin router can announce a shop refresh to
discord-gateway without duplicating logic.

The function is intentionally module-level (not on ShopService) to keep the
gateway HTTP call out of the DB-transaction service layer and to avoid pulling
``httpx`` into the service constructor path.

Usage::

    from utils.shop_announcement import announce_shop_refresh

    await announce_shop_refresh(
        caller_label="AdminRefresh",
        guild_id=guild_id,
        channel_id=shop_channel_id,      # may be None → logged & skipped
        bounty_hunter_role_id=role_id,   # may be None → no role mention
    )

Failures are **non-fatal** — errors are logged but do NOT propagate.
"""

import os
import traceback

import httpx
from shared.bblogger import get_logger

flogger = get_logger("shop-announcement")

_GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
_GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
_GATEWAY_BASE_URL = f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}/api/v1"


async def announce_shop_refresh(
    caller_label: str,
    guild_id: int,
    channel_id: int | None,
    bounty_hunter_role_id: int | None = None,
    tier: str | None = None,
) -> None:
    """POST a shop-refresh announcement to the discord-gateway channel messages endpoint.

    POSTs to ``POST /api/v1/channels/{channel_id}/messages`` with an
    EmbedPayload as the request body (matching ``MessageCreateRequest`` schema).

    The announcement is posted to the shop channel (``shop_channel_id``)
    so all players are notified of the shop restock.

    If ``channel_id`` is None, a warning is logged and the announcement
    is skipped — no shop channel has been configured for this guild yet.

    If ``bounty_hunter_role_id`` is set, a ``<@&{role_id}>`` mention is
    placed in ``text_content`` (plain text alongside the embed) so that
    Discord recognises it as an actual role mention and notifies members.
    Role mentions inside embed descriptions are NOT parsed by Discord.

    Failures are logged but do NOT propagate — a failed announcement is
    non-fatal for the refresh operation.

    Args:
        caller_label: A short identifier for log messages (e.g. "ShopRefreshJob[id]",
            "AdminRefresh").
        guild_id: The Discord guild ID being announced to.
        channel_id: The ``shop_channel_id`` from the guild config. If None the
            announcement is silently skipped with a warning.
        bounty_hunter_role_id: Optional role to mention in ``text_content``. When
            None no role ping is included.
        tier: Optional tier name (e.g. "Bronze"). When provided, the announcement
            targets that specific tier. When None, the announcement covers all tiers.
    """
    if channel_id is None:
        flogger.warning(f"{caller_label} guild={guild_id}: shop_channel_id not configured, skipping announcement")
        return

    if tier is not None:
        description = (
            f"The {tier} shop has been restocked with new items. "
            "Check out the latest offerings and upgrade your loadout!"
        )
        field_name = "Tier Refreshed"
        field_value = tier
    else:
        description = (
            "The guild shop has been restocked with new items across all tiers. "
            "Check out the latest offerings and upgrade your loadout!"
        )
        field_name = "Tiers Refreshed"
        field_value = "Bronze · Silver · Gold · Platinum"

    announcement = {
        "content": {  # embed payload
            "title": "🛒 Shop Refreshed!",
            "description": description,
            "color": 3447003,  # Blue (#3498DB)
            "fields": [
                {"name": field_name, "value": field_value, "inline": False},
            ],
            "footer_text": "Use /shop to browse!",
        },
        "text_content": f"<@&{bounty_hunter_role_id}>" if bounty_hunter_role_id else None,
        "message_type": "default",
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_GATEWAY_BASE_URL}/channels/{channel_id}/messages",
                json=announcement,
                timeout=10,
            )
        resp.raise_for_status()
        flogger.info(f"{caller_label} announced shop refresh for guild={guild_id} to channel={channel_id}")
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(
            f"{caller_label} failed to announce shop refresh for guild={guild_id} to channel={channel_id}: {e}"
        )
        flogger.trace(traceback.format_exc())
