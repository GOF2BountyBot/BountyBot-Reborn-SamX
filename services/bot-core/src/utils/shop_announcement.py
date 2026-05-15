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

    # New per-tier call with inventory items:
    await announce_shop_refresh(
        caller_label="ShopRefreshJob[id]",
        guild_id=guild_id,
        channel_id=shop_channel_id,
        bounty_hunter_role_id=role_id,
        tier="Bronze",
        items=refreshed_items,
        tech_level=5,
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

# ---------------------------------------------------------------------------
# Tier → embed colour mapping
# ---------------------------------------------------------------------------

_TIER_COLORS: dict[str, int] = {
    "bronze": 13467442,   # #CD7F32
    "silver": 12632256,   # #C0C0C0
    "gold": 16766720,     # #FFD700
    "platinum": 15066082,  # #E5E4E2
}
_DEFAULT_SHOP_COLOR = 3447003  # #3498DB — existing blue, used when tier is None

# ---------------------------------------------------------------------------
# Item-type display order and labels
# ---------------------------------------------------------------------------

_ITEM_TYPE_DISPLAY: list[tuple[str, str]] = [
    ("ship",             "🚀 Ships"),
    ("primary_weapon",   "🔫 Primary Weapons"),
    ("secondary_weapon", "💥 Secondary Weapons"),
    ("turret_weapon",    "🌀 Turret Weapons"),
    ("module",           "⚙️ Modules"),
]


# ---------------------------------------------------------------------------
# Private helpers for inventory embed construction
# ---------------------------------------------------------------------------


def _get_item_attr(item, attr: str):
    """Get attribute from ORM object or dict."""
    val = getattr(item, attr, None)
    if val is None and isinstance(item, dict):
        val = item.get(attr)
    return val


def _format_item_line(item) -> str:
    name = _get_item_attr(item, "item_name") or "Unknown"
    price = _get_item_attr(item, "price") or 0
    qty = _get_item_attr(item, "quantity") or 1
    return f"{name} — {price:,}c (x{qty})"


def _truncate_lines(lines: list[str], cap: int) -> tuple[str, int]:
    out: list[str] = []
    used = 0
    for i, ln in enumerate(lines):
        added = len(ln) + (1 if out else 0)
        if used + added > cap - 25 and i < len(lines):
            return "\n".join(out), len(lines) - len(out)
        out.append(ln)
        used += added
    return "\n".join(out), 0


def _build_inventory_fields(items: list) -> list[dict]:
    grouped: dict[str, list] = {}
    for it in items or []:
        t = _get_item_attr(it, "item_type") or "unknown"
        grouped.setdefault(t, []).append(it)

    fields: list[dict] = []
    for item_type, label in _ITEM_TYPE_DISPLAY:
        bucket = grouped.get(item_type) or []
        if not bucket:
            continue
        lines = [_format_item_line(it) for it in bucket]
        value, dropped = _truncate_lines(lines, 1024)
        if dropped:
            value = f"{value}\n… and {dropped} more"
        fields.append({"name": label, "value": value, "inline": False})
    return fields


async def announce_shop_refresh(
    caller_label: str,
    guild_id: int,
    channel_id: int | None,
    bounty_hunter_role_id: int | None = None,
    tier: str | None = None,
    items: list | None = None,
    tech_level: int | None = None,
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

    When ``items`` is not None (new inventory-aware path), the embed title,
    colour, description, and fields reflect the specific tier and its
    stocked inventory.  When ``items`` is None (legacy path), the existing
    single-call behaviour is preserved unchanged.

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
        items: Optional list of GuildShop ORM objects or plain dicts representing
            the refreshed inventory.  When not None, the new inventory-aware embed
            path is used (with tier colour + item fields).  When None, the legacy
            single-announcement path is preserved.
        tech_level: Optional tech level shown in the embed title when ``items`` is
            not None and ``tier`` is not None.
    """
    if channel_id is None:
        flogger.warning(f"{caller_label} guild={guild_id}: shop_channel_id not configured, skipping announcement")
        return

    if items is not None:
        # ── New inventory-aware path ──────────────────────────────────────────
        color = _TIER_COLORS.get(tier.lower(), _DEFAULT_SHOP_COLOR) if tier else _DEFAULT_SHOP_COLOR

        if tier and tech_level is not None:
            title = f"🛒 {tier.title()} Shop Refreshed — Tech Level {tech_level}"
        elif tier:
            title = f"🛒 {tier.title()} Shop Refreshed"
        else:
            title = "🛒 Shop Refreshed!"

        if not items:
            description = f"The {tier.title()} shop refreshed but no items are currently stocked." if tier else (
                "The shop refreshed but no items are currently stocked."
            )
            embed_fields: list[dict] = []
        else:
            description = f"The {tier.title()} shop has been restocked. Browse with /shop." if tier else (
                "The shop has been restocked. Browse with /shop."
            )
            embed_fields = _build_inventory_fields(items)

        announcement = {
            "content": {
                "title": title,
                "description": description,
                "color": color,
                "fields": embed_fields,
                "footer_text": "Use /shop to browse · /buy <item> to purchase",
            },
            "text_content": f"<@&{bounty_hunter_role_id}>" if bounty_hunter_role_id else None,
            "message_type": "default",
        }
    else:
        # ── Legacy path — keep existing behaviour unchanged ───────────────────
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
