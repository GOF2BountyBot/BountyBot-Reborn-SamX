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

These helpers are pure functions (no module-level state) and accept the
caller's ``httpx.AsyncClient`` so tests can substitute their own client
without monkeypatching.
"""

from __future__ import annotations

import discord
import httpx
from discord import app_commands

from utils.autocomplete_utils import normalize_for_search

# Discord hard limit on autocomplete choice count.
_MAX_CHOICES = 25


async def resolve_player_id(
    http_client: httpx.AsyncClient,
    api_base: str,
    user_id: int,
    guild_id: int,
    *,
    timeout: float = 3.0,
) -> int | None:
    """Resolve a Discord user to their bot-core player ID via POST /players/.

    Swallows ALL exceptions (including guild-not-configured 400s) and
    returns ``None``.  Autocomplete must never raise or show errors; the
    command handler is responsible for surfacing configuration issues to
    the user when the command actually runs.
    """
    try:
        resp = await http_client.post(
            f"{api_base}/players/",
            json={"discord_id": user_id, "guild_id": guild_id, "discord_username": None},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json().get("id")
    except Exception:  # pylint: disable=broad-exception-caught
        return None


async def player_ships_autocomplete(
    http_client: httpx.AsyncClient,
    api_base: str,
    interaction: discord.Interaction,
    current: str,
    *,
    exclude_active: bool = False,
    timeout: float = 3.0,
) -> list[app_commands.Choice[str]]:
    """Return Discord autocomplete choices of the invoking player's ships.

    Value format : ``str(ship_id)`` — callers should ``int()`` when needed.
    Label format : ``"ShipName (Nickname)"`` prefixed with ``"🟢 "`` when
                   the ship is the player's active ship.
    Filter       : accent/apostrophe-insensitive substring match on the label.

    Args:
        http_client: ``httpx.AsyncClient`` used for API calls (short timeout).
        api_base: bot-core API base URL (e.g. ``http://bot-core:8000/api/v1``).
        interaction: Discord interaction; used for ``user.id`` + ``guild_id``.
        current: Current (partial) text typed by the user in the slash-command
            parameter; empty string matches everything.
        exclude_active: When True, the active ship is omitted from the choices
            (used by flows that forbid operating on the active ship, e.g. ``/give``).
        timeout: Per-request timeout in seconds.

    Returns:
        Up to 25 matching choices, or ``[]`` on any error.
    """
    try:
        player_id = await resolve_player_id(
            http_client, api_base, interaction.user.id, interaction.guild_id, timeout=timeout
        )
        if not player_id:
            return []

        ships_resp = await http_client.get(f"{api_base}/ships/player/{player_id}", timeout=timeout)
        ships_resp.raise_for_status()
        ships = ships_resp.json()

        norm_current = normalize_for_search(current)
        choices: list[app_commands.Choice[str]] = []
        for ship in ships:
            ship_name = ship.get("ship_name") or ""
            ship_id_val = ship.get("id")
            if not ship_name or ship_id_val is None:
                continue
            if exclude_active and ship.get("is_active"):
                continue

            nickname = ship.get("nickname") or ""
            label = f"{ship_name} ({nickname})" if nickname else ship_name
            if ship.get("is_active"):
                label = f"🟢 {label}"

            if norm_current in normalize_for_search(label):
                choices.append(app_commands.Choice(name=label[:100], value=str(ship_id_val)))
        return choices[:_MAX_CHOICES]
    except Exception:  # pylint: disable=broad-exception-caught
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

    Value format : ``item_name`` (matches how ``/item`` and ``/sell`` pass names).
    Label format : ``"ItemName (TypeLabel)"`` plus ``" x<qty>"`` when
                   ``quantity > 1``.
    Filter       : accent/apostrophe-insensitive substring match on the label.

    Args:
        http_client: ``httpx.AsyncClient`` used for API calls (short timeout).
        api_base: bot-core API base URL.
        interaction: Discord interaction; used for ``user.id`` + ``guild_id``.
        current: Current (partial) text typed by the user; empty matches all.
        item_type_filter: When provided, only items whose ``item_type`` matches
            this string are returned.  Useful to scope ``/item`` autocomplete
            to a specific type already chosen by the user.
        timeout: Per-request timeout in seconds.

    Returns:
        Up to 25 matching choices, or ``[]`` on any error.
    """
    try:
        player_id = await resolve_player_id(
            http_client, api_base, interaction.user.id, interaction.guild_id, timeout=timeout
        )
        if not player_id:
            return []

        inv_resp = await http_client.get(f"{api_base}/inventory/player/{player_id}", timeout=timeout)
        inv_resp.raise_for_status()
        items = inv_resp.json()

        norm_current = normalize_for_search(current)
        choices: list[app_commands.Choice[str]] = []
        seen: set[str] = set()
        for item in items:
            item_name = item.get("item_name") or ""
            item_type = item.get("item_type") or ""
            quantity = item.get("quantity") or 0
            if not item_name or item_name in seen:
                continue
            if item_type_filter and item_type != item_type_filter:
                continue

            qty_suffix = f" x{quantity}" if quantity and quantity > 1 else ""
            label = f"{item_name} ({item_type.title() or 'Item'}){qty_suffix}"

            if norm_current in normalize_for_search(label):
                seen.add(item_name)
                choices.append(app_commands.Choice(name=label[:100], value=item_name))
        return choices[:_MAX_CHOICES]
    except Exception:  # pylint: disable=broad-exception-caught
        return []
