"""Shared autocomplete helpers for Discord Gateway cogs.

This module centralises autocomplete logic that was previously duplicated
across cogs (notably the player-ship lookup used by ``/setactive``,
``/ship``, and ``/nickname``).  All helpers degrade silently on any error
because Discord autocomplete has no user-visible error surface — returning
``[]`` is the correct fallback so the user simply sees "no matching
options".

Public helpers:
    - :func:`resolve_player_id` — upsert-style player lookup
    - :func:`player_ships_autocomplete` — choices of the invoking player's ships
    - :func:`player_inventory_autocomplete` — choices of the player's inventory items
    - :func:`player_equippable_autocomplete` — items in inventory NOT yet equipped (A.37)
    - :func:`player_equipped_autocomplete` — items currently equipped on active ship (A.37)

Phase 4 note: All helpers now read from ``autocomplete_state`` shared caches
instead of making HTTP calls per keystroke.  The ``http_client`` and
``api_base`` parameters are retained in all public signatures for backward
compatibility with existing call sites — they are accepted but **unused**.
Actual HTTP is performed by the bot-owned client via ``autocomplete_state``.

A.38 surface gating:
    ``_CURRENTLY_EQUIPPABLE_INVENTORY_TYPES`` mirrors
    ``GameConstants.CURRENTLY_ENABLED_TYPES`` (minus "ship") from bot-core.
    Both must be updated together when secondary weapons are enabled.
    Cross-reference: services/bot-core/src/services/game_constants.py
        GameConstants.CURRENTLY_ENABLED_TYPES
"""

from __future__ import annotations

import discord
import httpx
from discord import app_commands
from shared import bblogger

import utils.autocomplete_state as autocomplete_state
from utils.autocomplete_utils import normalize_for_search

logger = bblogger.get_logger("discord-gateway-autocomplete-helpers")

# Discord hard limit on autocomplete choice count.
_MAX_CHOICES = 25

# Concrete item types that can be equipped via the user-facing /equip surface TODAY.
# Mirrors bot-core GameConstants.CURRENTLY_ENABLED_TYPES minus "ship".
# Cross-reference: services/bot-core/src/services/game_constants.py
# When secondary weapons ship: add "secondary_weapon" here AND update bot-core's constant.
_CURRENTLY_EQUIPPABLE_INVENTORY_TYPES: frozenset[str] = frozenset({"primary_weapon", "turret_weapon", "module"})


async def resolve_player_id(
    http_client: httpx.AsyncClient,
    api_base: str,
    user_id: int,
    guild_id: int,
    *,
    timeout: float = 3.0,
) -> int | None:
    """Resolve a Discord user to their bot-core player ID.

    Phase 4: Reads from ``autocomplete_state.player_cache`` (peek-first).
    HTTP params retained for backward compat. Actual HTTP is performed by the
    bot-owned client via autocomplete_state.

    On warm cache hit: returns ``player['id']`` immediately with no I/O.
    On cold miss: schedules a background refresh and returns ``None``.

    Swallows ALL exceptions and returns ``None``.  Autocomplete must never
    raise or show errors; the command handler is responsible for surfacing
    configuration issues to the user when the command actually runs.
    """
    _ = http_client  # unused — backward compat only
    _ = api_base  # unused — backward compat only
    try:
        if autocomplete_state.player_cache is None:
            # Not yet initialized — can't serve from cache.
            return None

        cached = autocomplete_state.player_cache.peek((guild_id, user_id))
        if cached is None:
            cached = await autocomplete_state.player_cache.get_with_timeout((guild_id, user_id), timeout=1.0)
        if cached is not None:
            return cached.get("id")

        return None

    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning(
            "resolve_player_id: exception resolving player; helper=resolve_player_id "
            f"user_id={user_id} guild_id={guild_id}",
            exc_info=True,
        )
        return None


async def player_ships_autocomplete(
    http_client: httpx.AsyncClient,
    api_base: str,
    interaction: discord.Interaction,
    current: str,
    *,
    exclude_active: bool = False,
    show_active_indicator: bool = True,
    timeout: float = 3.0,
) -> list[app_commands.Choice[str]]:
    """Return Discord autocomplete choices of the invoking player's ships.

    Phase 4: Reads from ``autocomplete_state.player_cache`` and
    ``autocomplete_state.ships_cache`` instead of making HTTP calls.
    HTTP params retained for backward compat.

    Value format : ``str(ship_id)`` — callers should ``int()`` when needed.
    Label format : ``"ShipName (Nickname)"`` prefixed with ``"🟢 "`` when
                   the ship is the player's active ship and ``show_active_indicator``
                   is True.
    Filter       : accent/apostrophe-insensitive substring match on the label.

    Args:
        http_client: Unused; retained for backward compatibility with call sites.
        api_base: Unused; retained for backward compatibility with call sites.
        interaction: Discord interaction; used for ``user.id`` + ``guild_id``.
        current: Current (partial) text typed by the user in the slash-command
            parameter; empty string matches everything.
        exclude_active: When True, the active ship is omitted from the choices
            (used by flows that forbid operating on the active ship, e.g. ``/give``).
        show_active_indicator: When True (default), prefixes the active ship's label
            with ``"🟢 "`` to indicate it is the active ship. Set to False for
            selection-only contexts (e.g. ``/ship``, ``/nickname``) where the
            indicator clutters the dropdown without adding actionable information.
        timeout: Unused; retained for backward compatibility with call sites.

    Returns:
        Up to 25 matching choices, or ``[]`` on any error.
    """
    _ = http_client  # unused — backward compat only
    _ = api_base  # unused — backward compat only
    try:
        user_id = interaction.user.id
        guild_id = interaction.guild_id

        # Resolve player_id from player_cache.
        if autocomplete_state.player_cache is None:
            return []
        player_entry = autocomplete_state.player_cache.peek((guild_id, user_id))
        if player_entry is None:
            player_entry = await autocomplete_state.player_cache.get_with_timeout((guild_id, user_id), timeout=1.0)
        if player_entry is None:
            return []
        player_id = player_entry.get("id")
        if not player_id:
            return []

        # Peek ships_cache.
        if autocomplete_state.ships_cache is None:
            return []
        ships = autocomplete_state.ships_cache.peek((guild_id, player_id))
        if ships is None:
            ships = await autocomplete_state.ships_cache.get_with_timeout((guild_id, player_id), timeout=1.0)
        if ships is None:
            return []

        norm_current = normalize_for_search(current)
        choices: list[app_commands.Choice[str]] = []
        for nc in ships:
            raw = nc.raw
            if exclude_active and raw.get("is_active"):
                continue

            ship_name = raw.get("ship_name") or raw.get("name") or ""
            ship_id_val = raw.get("id") or raw.get("player_ship_id")
            if not ship_name or ship_id_val is None:
                continue

            nickname = raw.get("nickname") or ""
            label = f"{ship_name} ({nickname})" if nickname else ship_name
            if raw.get("is_active") and show_active_indicator:
                label = f"🟢 {label}"

            if norm_current in normalize_for_search(label):
                choices.append(app_commands.Choice(name=label[:100], value=str(ship_id_val)))

        return choices[:_MAX_CHOICES]

    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning(
            "player_ships_autocomplete: exception building ship choices; helper=player_ships_autocomplete "
            f"user_id={getattr(getattr(interaction, 'user', None), 'id', None)} "
            f"guild_id={getattr(interaction, 'guild_id', None)}",
            exc_info=True,
        )
        return []


async def player_inventory_autocomplete(
    http_client: httpx.AsyncClient,
    api_base: str,
    interaction: discord.Interaction,
    current: str,
    *,
    item_type_filter: str | None = None,
    timeout: float = 3.0,
) -> list[app_commands.Choice[str]]:
    """Return Discord autocomplete choices of the invoking player's inventory.

    Phase 4: Reads from ``autocomplete_state.player_cache`` and
    ``autocomplete_state.inventory_cache`` instead of making HTTP calls.
    HTTP params retained for backward compat.

    Value format : ``item_name`` (matches how ``/item`` and ``/sell`` pass names).
    Label format : ``"ItemName (TypeLabel)"`` plus ``" x<qty>"`` when
                   ``quantity > 1``.
    Filter       : accent/apostrophe-insensitive substring match on the label.

    Args:
        http_client: Unused; retained for backward compatibility with call sites.
        api_base: Unused; retained for backward compatibility with call sites.
        interaction: Discord interaction; used for ``user.id`` + ``guild_id``.
        current: Current (partial) text typed by the user; empty matches all.
        item_type_filter: When provided, only items whose ``item_type`` matches
            this string are returned.  Useful to scope ``/item`` autocomplete
            to a specific type already chosen by the user.
        timeout: Unused; retained for backward compatibility with call sites.

    Returns:
        Up to 25 matching choices, or ``[]`` on any error.
    """
    _ = http_client  # unused — backward compat only
    _ = api_base  # unused — backward compat only
    try:
        user_id = interaction.user.id
        guild_id = interaction.guild_id

        # Resolve player_id from player_cache.
        if autocomplete_state.player_cache is None:
            return []
        player_entry = autocomplete_state.player_cache.peek((guild_id, user_id))
        if player_entry is None:
            player_entry = await autocomplete_state.player_cache.get_with_timeout((guild_id, user_id), timeout=1.0)
        if player_entry is None:
            return []
        player_id = player_entry.get("id")
        if not player_id:
            return []

        # Peek inventory_cache.
        if autocomplete_state.inventory_cache is None:
            return []
        items = autocomplete_state.inventory_cache.peek((guild_id, player_id))
        if items is None:
            items = await autocomplete_state.inventory_cache.get_with_timeout((guild_id, player_id), timeout=1.0)
        if items is None:
            return []

        norm_current = normalize_for_search(current)
        choices: list[app_commands.Choice[str]] = []
        seen: set[str] = set()
        for nc in items:
            raw = nc.raw
            item_name = raw.get("item_name") or ""
            item_type = raw.get("item_type") or ""
            quantity = raw.get("quantity") or 0

            if not item_name or item_name in seen:
                continue
            if item_type_filter and item_type != item_type_filter:
                continue

            qty_suffix = f" x{quantity}" if quantity and quantity > 1 else ""
            label = f"{item_name} ({item_type.replace('_', ' ').title() or 'Item'}){qty_suffix}"

            if norm_current in normalize_for_search(label):
                seen.add(item_name)
                choices.append(app_commands.Choice(name=label[:100], value=item_name))

        return choices[:_MAX_CHOICES]

    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning(
            "player_inventory_autocomplete: exception building inventory choices; "
            "helper=player_inventory_autocomplete "
            f"user_id={getattr(getattr(interaction, 'user', None), 'id', None)} "
            f"guild_id={getattr(interaction, 'guild_id', None)}",
            exc_info=True,
        )
        return []


async def player_equippable_autocomplete(
    http_client: httpx.AsyncClient,
    api_base: str,
    interaction: discord.Interaction,
    current: str,
    *,
    timeout: float = 3.0,
) -> list[app_commands.Choice[str]]:
    """Return Discord autocomplete choices of items the player can equip.

    Phase 4: Reads from ``autocomplete_state`` caches instead of making HTTP calls.
    HTTP params retained for backward compat.

    An item is "equippable" if:
    - Its ``item_type`` is in ``_CURRENTLY_EQUIPPABLE_INVENTORY_TYPES`` (i.e.
      primary_weapon, turret_weapon, or module — NOT secondary_weapon or ship today).
    - Its ``quantity`` (cargo copies) is > 0.  ``player_inventories.quantity`` is
      CARGO-ONLY — equipped copies are stored in the ship loadout JSON and do NOT
      reduce the cargo quantity.  A player with 1 cargo copy AND 1 equipped copy
      of the same item still has quantity=1 in cargo, so that copy can be equipped
      on another slot.  The correct gate is therefore ``quantity <= 0``, NOT an
      equipped-names check (B.41 / AGENTS.md).

    Value format : ``item_name``
    Label format : ``"ItemName (TypeLabel) xN"`` (quantity suffix when > 1)
    Filter       : accent/apostrophe-insensitive substring match.

    Args:
        http_client: Unused; retained for backward compatibility with call sites.
        api_base: Unused; retained for backward compatibility with call sites.
        interaction: Discord interaction.
        current: Partial text typed by the user; empty matches all.
        timeout: Unused; retained for backward compatibility with call sites.

    Returns:
        Up to 25 matching choices, or ``[]`` on any error.
    """
    _ = http_client  # unused — backward compat only
    _ = api_base  # unused — backward compat only
    try:
        user_id = interaction.user.id
        guild_id = interaction.guild_id

        # Resolve player_id from player_cache.
        if autocomplete_state.player_cache is None:
            return []
        player_entry = autocomplete_state.player_cache.peek((guild_id, user_id))
        if player_entry is None:
            player_entry = await autocomplete_state.player_cache.get_with_timeout((guild_id, user_id), timeout=1.0)
        if player_entry is None:
            return []
        player_id = player_entry.get("id")
        if not player_id:
            return []

        # Peek inventory_cache — need equippable items.
        if autocomplete_state.inventory_cache is None:
            return []
        items = autocomplete_state.inventory_cache.peek((guild_id, player_id))
        if items is None:
            items = await autocomplete_state.inventory_cache.get_with_timeout((guild_id, player_id), timeout=1.0)
        if items is None:
            return []

        norm_current = normalize_for_search(current)
        choices: list[app_commands.Choice[str]] = []
        seen: set[str] = set()
        for nc in items:
            raw = nc.raw
            item_name = raw.get("item_name") or ""
            item_type = raw.get("item_type") or ""
            quantity = raw.get("quantity") or 0

            if not item_name or item_name in seen:
                continue
            if item_type not in _CURRENTLY_EQUIPPABLE_INVENTORY_TYPES:
                continue
            if quantity <= 0:
                continue

            qty_suffix = f" x{quantity}" if quantity and quantity > 1 else ""
            label = f"{item_name} ({item_type.replace('_', ' ').title()}){qty_suffix}"
            if norm_current in normalize_for_search(label):
                seen.add(item_name)
                choices.append(app_commands.Choice(name=label[:100], value=item_name))

        return choices[:_MAX_CHOICES]

    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning(
            "player_equippable_autocomplete: exception building equippable choices; "
            "helper=player_equippable_autocomplete "
            f"user_id={getattr(getattr(interaction, 'user', None), 'id', None)} "
            f"guild_id={getattr(interaction, 'guild_id', None)}",
            exc_info=True,
        )
        return []


async def player_equipped_autocomplete(
    http_client: httpx.AsyncClient,
    api_base: str,
    interaction: discord.Interaction,
    current: str,
    *,
    timeout: float = 3.0,
) -> list[app_commands.Choice[str]]:
    """Return Discord autocomplete choices of items currently equipped on the active ship.

    Phase 4: Reads from ``autocomplete_state`` caches instead of making HTTP calls.
    HTTP params retained for backward compat.

    Includes weapons, modules, turrets, and secondary_weapons (all slots).
    Reads are NOT gated — if a secondary_weapon is somehow equipped, it is shown.

    Value format : ``item_name``
    Label format : ``item_name``
    Filter       : accent/apostrophe-insensitive substring match.

    Args:
        http_client: Unused; retained for backward compatibility with call sites.
        api_base: Unused; retained for backward compatibility with call sites.
        interaction: Discord interaction.
        current: Partial text typed by the user; empty matches all.
        timeout: Unused; retained for backward compatibility with call sites.

    Returns:
        Up to 25 matching choices, or ``[]`` on any error.
    """
    _ = http_client  # unused — backward compat only
    _ = api_base  # unused — backward compat only
    try:
        user_id = interaction.user.id
        guild_id = interaction.guild_id

        # Resolve player_id from player_cache.
        if autocomplete_state.player_cache is None:
            return []
        player_entry = autocomplete_state.player_cache.peek((guild_id, user_id))
        if player_entry is None:
            player_entry = await autocomplete_state.player_cache.get_with_timeout((guild_id, user_id), timeout=1.0)
        if player_entry is None:
            return []
        player_id = player_entry.get("id")
        if not player_id:
            return []

        # Peek ships_cache.
        if autocomplete_state.ships_cache is None:
            return []
        ships = autocomplete_state.ships_cache.peek((guild_id, player_id))
        if ships is None:
            ships = await autocomplete_state.ships_cache.get_with_timeout((guild_id, player_id), timeout=1.0)
        if ships is None:
            return []

        # Find active ship.
        active_ship_raw: dict | None = None
        for nc in ships:
            if nc.raw.get("is_active"):
                active_ship_raw = nc.raw
                break
        if not active_ship_raw:
            return []

        # Collect all equipped items from all slots.
        equipped: list[str] = []
        equipped.extend(active_ship_raw.get("weapons") or [])
        equipped.extend(active_ship_raw.get("modules") or [])
        equipped.extend(active_ship_raw.get("turrets") or [])
        equipped.extend(active_ship_raw.get("secondary_weapons") or [])

        norm_current = normalize_for_search(current)
        choices: list[app_commands.Choice[str]] = []
        seen: set[str] = set()
        for item_name in equipped:
            if not item_name or item_name in seen:
                continue
            if norm_current in normalize_for_search(item_name):
                seen.add(item_name)
                choices.append(app_commands.Choice(name=item_name, value=item_name))

        return choices[:_MAX_CHOICES]

    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning(
            "player_equipped_autocomplete: exception building equipped choices; "
            "helper=player_equipped_autocomplete "
            f"user_id={getattr(getattr(interaction, 'user', None), 'id', None)} "
            f"guild_id={getattr(interaction, 'guild_id', None)}",
            exc_info=True,
        )
        return []
