"""Module-level shared caches for discord-gateway autocomplete.

All cogs share one view of per-user state. Invalidation calls in one cog
(e.g. /buy invalidating inventory) are immediately visible to autocomplete
handlers in other cogs (e.g. /equip).

init() must be called once from bot.py lifespan using the bot-owned
httpx.AsyncClient. The bot owns this client's lifecycle (close on shutdown).

Phase 2 note: This module is **dormant infrastructure** — it initialises caches
and wires up refresh functions, but nothing calls these helpers yet. Bot
behaviour is unchanged until Phase 4 rewires the autocomplete helpers to
read from these shared caches.
"""

from __future__ import annotations

import os
from typing import NamedTuple

import httpx
from cogs._shared.autocomplete_cache import AutocompleteCache
from shared import bblogger

from utils.autocomplete_utils import normalize_for_search

flogger = bblogger.get_logger("discord-gateway-autocomplete-state")

# ---------------------------------------------------------------------------
# NormalizedChoice — pre-computed choice for hot-path substring scan
# ---------------------------------------------------------------------------


class NormalizedChoice(NamedTuple):
    """A pre-normalized autocomplete choice entry.

    Stores the display label, command value, a pre-computed search key
    (result of :func:`normalize_for_search` applied to ``label``), and
    the raw API dict for fields that command paths may need.

    Pre-computing ``norm`` at cache-fill time means the hot autocomplete
    loop only performs a pure string ``in`` check on already-lowercased,
    diacritic-stripped strings — zero NFKD allocations per keystroke.
    """

    label: str  # display label, e.g. "Laser Cannon (Primary Weapon) [x2]"
    value: str  # Choice value passed to the command
    norm: str  # pre-computed normalize_for_search(label)
    raw: dict  # underlying dict (item_type, is_active, nickname, qty, etc.)


# ---------------------------------------------------------------------------
# Module-level state — NOT instantiated at import time (lazy init)
# ---------------------------------------------------------------------------

_initialized: bool = False
_http_client: httpx.AsyncClient | None = None
_api_base: str | None = None

# Cache instances (None until init() is called)
player_cache: AutocompleteCache[tuple[int, int], dict] | None = None
inventory_cache: AutocompleteCache[tuple[int, int], list[NormalizedChoice]] | None = None
ships_cache: AutocompleteCache[tuple[int, int], list[NormalizedChoice]] | None = None


# ---------------------------------------------------------------------------
# Internal refresh functions (called by AutocompleteCache.get() on cold miss)
# ---------------------------------------------------------------------------


async def _refresh_player(key: tuple[int, int]) -> dict:
    """Refresh a single player record from bot-core.

    Args:
        key: ``(guild_id, user_id)`` tuple.

    Returns:
        Full player dict as returned by ``POST /players/``.

    Raises:
        Any httpx exception — caller (AutocompleteCache) applies stale-on-error
        policy and returns last-known-good value when available.
    """
    if _http_client is None or _api_base is None:
        raise RuntimeError("autocomplete_state.init() must be called before use")

    guild_id, user_id = key
    resp = await _http_client.post(
        f"{_api_base}/players/",
        json={"discord_id": user_id, "guild_id": guild_id, "discord_username": None},
        timeout=3.0,
    )
    resp.raise_for_status()
    return resp.json()


async def _refresh_inventory(key: tuple[int, int]) -> list[NormalizedChoice]:
    """Refresh inventory for a player from bot-core.

    Args:
        key: ``(guild_id, player_id)`` tuple — note: player_id not user_id.

    Returns:
        List of :class:`NormalizedChoice` items with pre-computed ``norm`` fields.

    Raises:
        Any httpx exception — caller applies stale-on-error policy.
    """
    if _http_client is None or _api_base is None:
        raise RuntimeError("autocomplete_state.init() must be called before use")

    _guild_id, player_id = key
    resp = await _http_client.get(
        f"{_api_base}/inventory/player/{player_id}",
        timeout=3.0,
    )
    resp.raise_for_status()
    items = resp.json()

    choices: list[NormalizedChoice] = []
    for item in items:
        item_name = item.get("item_name") or ""
        item_type = item.get("item_type") or ""
        quantity = item.get("quantity") or 0
        if not item_name:
            continue

        qty_suffix = f" [x{quantity}]" if quantity and quantity > 1 else ""
        label = f"{item_name} ({item_type.replace('_', ' ').title()}){qty_suffix}"
        value = str(item.get("id", item_name))
        norm = normalize_for_search(label)

        choices.append(NormalizedChoice(label=label, value=value, norm=norm, raw=item))

    return choices


async def _refresh_ships(key: tuple[int, int]) -> list[NormalizedChoice]:
    """Refresh player ships from bot-core.

    Args:
        key: ``(guild_id, player_id)`` tuple.

    Returns:
        List of :class:`NormalizedChoice` for player ships, with ``is_active``
        state reflected in the label.

    Raises:
        Any httpx exception — caller applies stale-on-error policy.
    """
    if _http_client is None or _api_base is None:
        raise RuntimeError("autocomplete_state.init() must be called before use")

    _guild_id, player_id = key
    resp = await _http_client.get(
        f"{_api_base}/ships/player/{player_id}",
        timeout=3.0,
    )
    resp.raise_for_status()
    ships = resp.json()

    choices: list[NormalizedChoice] = []
    for ship in ships:
        nickname = ship.get("nickname") or ""
        name = ship.get("name") or ship.get("ship_name") or ""
        display_name = nickname or name
        ship_type = ship.get("ship_type") or ship.get("type") or ""
        is_active = ship.get("is_active", False)

        active_prefix = "⚡ " if is_active else ""
        label = f"{display_name} ({active_prefix}{ship_type})"
        # Use player_ship_id (the join table ID) as the value
        value = str(ship.get("player_ship_id") or ship.get("id") or "")
        norm = normalize_for_search(label)

        choices.append(NormalizedChoice(label=label, value=value, norm=norm, raw=ship))

    return choices


# ---------------------------------------------------------------------------
# Public API: init()
# ---------------------------------------------------------------------------


def init(http_client: httpx.AsyncClient, api_base: str) -> None:
    """Initialise shared autocomplete state. Idempotent — first call wins.

    Must be called once from ``bot.py`` lifespan (or equivalent) before any
    other function in this module is used. Subsequent calls are no-ops.

    Args:
        http_client: Bot-owned ``httpx.AsyncClient``. The bot is responsible for
            closing this client on shutdown; ``autocomplete_state`` never closes it.
        api_base: bot-core API base URL (e.g. ``http://bot-core:8000/api/v1``).
    """
    global _initialized, _http_client, _api_base
    global player_cache, inventory_cache, ships_cache

    if _initialized:
        flogger.debug("autocomplete_state.init() called again — no-op (first-call-wins)")
        return

    _http_client = http_client
    _api_base = api_base

    # Read TTL and max_entries from environment
    player_ttl = float(os.environ.get("AUTOCOMPLETE_PLAYER_TTL_SECONDS", "900"))  # 15 min
    loadout_ttl = float(os.environ.get("AUTOCOMPLETE_LOADOUT_TTL_SECONDS", "600"))  # 10 min

    inventory_max_raw = os.environ.get("AUTOCOMPLETE_INVENTORY_MAX_ENTRIES")
    ships_max_raw = os.environ.get("AUTOCOMPLETE_SHIPS_MAX_ENTRIES")

    inventory_max: int | None = int(inventory_max_raw) if inventory_max_raw else None
    ships_max: int | None = int(ships_max_raw) if ships_max_raw else None

    player_cache = AutocompleteCache(
        ttl_seconds=player_ttl,
        refresh_fn=_refresh_player,
        name="player",
    )
    inventory_cache = AutocompleteCache(
        ttl_seconds=loadout_ttl,
        refresh_fn=_refresh_inventory,
        name="inventory",
        max_entries=inventory_max,
    )
    ships_cache = AutocompleteCache(
        ttl_seconds=loadout_ttl,
        refresh_fn=_refresh_ships,
        name="ships",
        max_entries=ships_max,
    )

    _initialized = True
    flogger.info(
        f"autocomplete_state initialised: player_ttl={player_ttl}s, "
        f"loadout_ttl={loadout_ttl}s, "
        f"inventory_max={inventory_max}, ships_max={ships_max}"
    )


# ---------------------------------------------------------------------------
# Guard helper
# ---------------------------------------------------------------------------


def _require_initialized() -> None:
    """Raise RuntimeError if init() has not been called."""
    if not _initialized:
        raise RuntimeError(
            "autocomplete_state.init() must be called before use. "
            "Call it once from bot.py lifespan with the bot-owned httpx.AsyncClient."
        )


# ---------------------------------------------------------------------------
# Async getters — for command paths that need to await fresh data
# ---------------------------------------------------------------------------


async def get_player(guild_id: int, user_id: int) -> dict | None:
    """Await fresh player data.

    Uses ``player_cache.get()`` which triggers a refresh on cold miss (via
    ``_refresh_player``).

    For autocomplete hot paths use ``player_cache.peek()`` directly to avoid
    awaiting; this function is intended for command paths where fresh data is
    required (e.g. resolving a player for a slash command that just triggered).

    Args:
        guild_id: Discord guild ID.
        user_id: Discord user ID (not bot-core player ID).

    Returns:
        Full player dict, or ``None`` on error or miss with no cached value.
    """
    _require_initialized()
    assert player_cache is not None  # guaranteed by _require_initialized
    return await player_cache.get((guild_id, user_id))


async def get_player_id(guild_id: int, user_id: int) -> int | None:
    """Convenience wrapper — returns ``player['id']`` or ``None``.

    Calls :func:`get_player` and extracts the ``"id"`` field. Returns ``None``
    if the player is absent from cache and the refresh fails.
    """
    player = await get_player(guild_id, user_id)
    if player is None:
        return None
    return player.get("id")


# ---------------------------------------------------------------------------
# Synchronous write-through helpers — call from command success paths
# ---------------------------------------------------------------------------


def set_player(guild_id: int, user_id: int, player: dict) -> None:
    """Write a fresh player dict into the cache.

    Call this from command success paths where the bot-core response already
    contains the fresh player record (e.g. after ``/promote`` or ``/profile``).
    Avoids a redundant round-trip on the next autocomplete keystroke.
    """
    _require_initialized()
    assert player_cache is not None
    player_cache.set((guild_id, user_id), player)


def set_inventory(guild_id: int, player_id: int, items: list[NormalizedChoice]) -> None:
    """Write a fresh inventory list into the cache.

    Args:
        guild_id: Discord guild ID.
        player_id: bot-core player ID (not Discord user ID).
        items: Pre-normalized inventory list (use :func:`_refresh_inventory`
            output or build manually from a fresh API response).
    """
    _require_initialized()
    assert inventory_cache is not None
    inventory_cache.set((guild_id, player_id), items)


def set_ships(guild_id: int, player_id: int, ships: list[NormalizedChoice]) -> None:
    """Write a fresh ships list into the cache.

    Args:
        guild_id: Discord guild ID.
        player_id: bot-core player ID (not Discord user ID).
        ships: Pre-normalized ships list.
    """
    _require_initialized()
    assert ships_cache is not None
    ships_cache.set((guild_id, player_id), ships)


# ---------------------------------------------------------------------------
# Synchronous invalidation helpers — call when command mutates state
# ---------------------------------------------------------------------------


def invalidate_player(guild_id: int, user_id: int) -> None:
    """Drop the cached player entry.

    Use this when a command mutates the player record but the fresh value is
    not available in the response (e.g. ``/buy`` changes credits but the
    response doesn't include the full updated player).
    """
    _require_initialized()
    assert player_cache is not None
    player_cache.invalidate((guild_id, user_id))


def invalidate_inventory(guild_id: int, player_id: int) -> None:
    """Drop the cached inventory entry for a player.

    Call after any command that changes the player's inventory
    (``/buy``, ``/sell``, ``/equip``, ``/unequip``, ``/give``).
    """
    _require_initialized()
    assert inventory_cache is not None
    inventory_cache.invalidate((guild_id, player_id))


def invalidate_ships(guild_id: int, player_id: int) -> None:
    """Drop the cached ships entry for a player.

    Call after any command that changes ship loadout or active ship
    (``/setactive``, ``/nickname``, ``/equip``, ``/unequip``).
    """
    _require_initialized()
    assert ships_cache is not None
    ships_cache.invalidate((guild_id, player_id))


def clear_all() -> None:
    """Drop all entries from all three shared caches.

    Called by ``/reload_autocomplete`` to force a full cold-restart of the
    shared cache state. The next autocomplete keystroke for any user will
    trigger a background refresh.
    """
    _require_initialized()
    assert player_cache is not None
    assert inventory_cache is not None
    assert ships_cache is not None
    player_cache.clear()
    inventory_cache.clear()
    ships_cache.clear()
    flogger.info("autocomplete_state: all three shared caches cleared")


# ---------------------------------------------------------------------------
# HTTP client accessor
# ---------------------------------------------------------------------------


def get_http_client() -> httpx.AsyncClient | None:
    """Return the bot-owned HTTP client stored at init() time.

    Allows cogs that need the shared client without importing it directly.
    Returns ``None`` before :func:`init` is called.
    """
    return _http_client


def get_api_base() -> str | None:
    """Return the API base URL set during init(). None if not initialized."""
    return _api_base
