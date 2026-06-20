"""Shared cargo-capacity helpers (LOOT_JOURNAL §5.4 / §5.5 / §7.1).

A single source of truth for "how much cargo is a player carrying and what is
their effective cap?" — used by both the T5 loot clamp (``BountyService``) and the
T7 over-cap lockout gate (``BountyService`` ``/check`` + ``DuelService`` duel
challenge/accept).

``effective_cap = active ship.cargo × Π(CompressorModule cargoMultiplier)``
(matches ``loadout_response_service``, §7.1).  ``current_load`` is the per-unit
``sum(PlayerInventory.quantity)`` (cargo only; equipped gear excluded, §7.4).  No
active ship ⇒ cap ``0``.
"""

import contextlib

from persist.models.module import Module
from persist.models.player_ship import PlayerShip
from persist.models.ship import Ship
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def compute_free_cargo(db: AsyncSession, inventory_repo, player) -> tuple[int, int, int]:
    """Return ``(free, current_load, effective_cap)`` for ``player``.

    ``free = effective_cap - current_load`` (may be negative when over cap).

    The T5 loot clamp calls this with ``player`` already held under a
    ``FOR UPDATE`` lock so the read + subsequent loot write are race-safe; the
    T7 over-cap gate calls it as a plain pre-resolution read (a stale borderline
    read self-corrects on the next command — LOOT_JOURNAL §5.5 C-3(b)).

    Args:
        db: Async session.
        inventory_repo: An ``InventoryRepository`` (cargo load source).
        player: A ``Player`` ORM object (needs ``id`` + ``active_ship_id``).

    Returns:
        ``(free, current_load, effective_cap)`` as ints.
    """
    # Current per-unit cargo load.
    inv_items = await inventory_repo.get_player_items(db, player.id)
    current_load = sum(int(getattr(i, "quantity", 0) or 0) for i in inv_items)

    active_ship_id = getattr(player, "active_ship_id", None)
    if not active_ship_id:
        return (0 - current_load, current_load, 0)

    player_ship = await db.get(PlayerShip, active_ship_id)
    if player_ship is None:
        return (0 - current_load, current_load, 0)

    ship_row = (await db.execute(select(Ship).where(Ship.name == player_ship.ship_name))).scalars().first()
    base_cargo = int(getattr(ship_row, "cargo", 0) or 0) if ship_row else 0

    # Compressor multiplier from equipped modules (only CompressorModule raises cap, §7.1).
    compressor_multiplier = 1.0
    for m_name in getattr(player_ship, "modules", None) or []:
        mod = (await db.execute(select(Module).where(Module.name == m_name))).scalars().first()
        if mod is None or getattr(mod, "type", None) != "CompressorModule":
            continue
        extra = mod.extra_atts if isinstance(getattr(mod, "extra_atts", None), dict) else {}
        raw_mult = extra.get("cargoMultiplier", extra.get("cargo_multiplier"))
        if raw_mult is not None:
            with contextlib.suppress(TypeError, ValueError):
                compressor_multiplier *= float(raw_mult)

    effective_cap = round(base_cargo * compressor_multiplier) if base_cargo else base_cargo
    return (effective_cap - current_load, current_load, effective_cap)


def is_over_cap(current_load: int, effective_cap: int) -> bool:
    """Over-cap is STRICTLY greater than cap (being exactly AT cap is allowed).

    LOOT_JOURNAL §5.5: only the loot step cares about ``free < 1``; the lockout
    gate fires only when ``current_load > effective_cap``.
    """
    return current_load > effective_cap
