"""
LoadoutConsistencyService — Package G B.19 choke-point.

Single canonical mutation point for every cross-table mutation that touches both
``player_ships.{weapons,modules,turrets,secondary_weapons}`` JSON and
``player_inventories`` rows.  The service enforces four hard invariants:

I1 — No item duplication across ships of one player.
I2 — No materialisation from nothing (every JSON entry has an inventory provenance).
I3 — Atomicity across both tables (caller owns the transaction; service uses commit=False).
I4 — Active ship is always within static slot limits.

Repositories remain dumb data-access; routers own transactions; services use
``commit=False``.  Direct calls to ``inventory_repo.add_item`` /
``inventory_repo.remove_item`` paired with ``player_ship_repo.add_equipment`` /
``remove_equipment`` outside this service are forbidden.

I3 enforcement (B.34 remediation, AC-5 + AC-6)
==============================================

Static enforcement: every router that calls into this service is checked
by ``tests/test_transaction_discipline.py``. Any route that calls a
flush-only method without wrapping in ``async with db.begin():`` (or
explicit commit) fails the test suite.

Runtime enforcement: every public method below carries the
``@requires_transaction`` decorator (``services/_transaction_guards.py``)
which raises RuntimeError immediately if invoked outside a transaction.

Consumer call-site map (verified at HEAD, 2026-04-30)
-----------------------------------------------------

| Method                             | Consumer                              | Wrapping site                           |
|------------------------------------|---------------------------------------|-----------------------------------------|
| equip_one                          | EquipmentService.equip_item           | api/routers/ships.py:423 (db.begin())   |
| equip_one (×3, starter)            | PlayerService._create_starter_loadout | api/routers/players.py:65 (B.34 fix)    |
| unequip_one                        | EquipmentService.unequip_item         | api/routers/ships.py:476 (db.begin())   |
| transfer_loadout_to_new_ship       | ShopService.purchase_ship             | api/routers/shops.py:152 (db.begin())   |
| evacuate_ship_loadout_to_inventory | ShopService.sell_ship                 | api/routers/shops.py:217 (db.begin())   |
| evacuate_ship_loadout_to_inventory | admin remove_ship                     | api/routers/admin.py:1036 (db.begin())  |
| evacuate_ship_loadout_to_inventory | ships.transfer_ship                   | api/routers/ships.py:597 (db.begin())   |
| reconcile_active_ship_slots        | ships.set_active_ship (legacy path)   | api/routers/ships.py:259 (db.begin())   |
| activate_ship                      | ships.set_active_ship                 | api/routers/ships.py:259 (db.begin())   |
| activate_ship                      | ShopService.purchase_ship             | api/routers/shops.py:152 (db.begin())   |
| repair_player                      | 0002_b19_repair_loadout_consistency   | Alembic migration runner                |
| repair_player                      | admin tooling (future)                | (must be wrapped per I3)                |

Each row above MUST be a wrapped consumer per I3. The runtime
``@requires_transaction`` guard catches any future regression that adds
a new consumer without wrapping; the static linter catches the most
common pattern (route function calling the choke-point directly or
transitively through equipment_service / shop_service / player_service).

See ``/proj/recon/B19-design.md`` for the original architectural rationale
and ``/proj/recon/B34-remediation-spec.md`` AC-5 / AC-6 for the
enforcement layers added in the B.34 remediation.
"""

from typing import Any

from persist.models.player_ship import PlayerShip
from persist.repositories.inventory_repository import InventoryRepository
from persist.repositories.item_repository import ItemRepository
from persist.repositories.player_repository import PlayerRepository
from persist.repositories.player_ship_repository import PlayerShipRepository
from persist.repositories.ship_repository import ShipRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

from services._transaction_guards import requires_transaction
from services.equipment_service import (
    _INVENTORY_TYPE_MAP,
    _SLOT_MAP,
    VALID_EQUIPMENT_TYPES,
    _item_type_to_equipment_category,
    item_discriminator_to_concrete_type,
)
from services.exceptions import InvalidItemTypeError
from services.game_constants import GameConstants

flogger = bblogger.get_logger("loadout-consistency-service")


# All four loadout slot kinds (used by transfer / evacuate / repair flows).
_SLOT_KINDS: tuple[str, ...] = ("weapons", "modules", "turrets", "secondary_weapons")


class LoadoutConsistencyService:
    """Choke-point for loadout↔inventory cross-table mutations.

    All methods accept ``db`` and never commit; the caller owns the
    transaction (the router-level ``db.begin()`` pattern).
    """

    def __init__(
        self,
        *,
        player_ship_repo: PlayerShipRepository | None = None,
        inventory_repo: InventoryRepository | None = None,
        item_repo: ItemRepository | None = None,
        ship_repo: ShipRepository | None = None,
        player_repo: PlayerRepository | None = None,
    ) -> None:
        # Optional constructor injection so callers (e.g.
        # ``EquipmentService.equip_item``) can share their already-mocked
        # repositories with the consistency service in unit tests.
        self.player_ship_repo = player_ship_repo if player_ship_repo is not None else PlayerShipRepository()
        self.inventory_repo = inventory_repo if inventory_repo is not None else InventoryRepository()
        self.item_repo = item_repo if item_repo is not None else ItemRepository()
        self.ship_repo = ship_repo if ship_repo is not None else ShipRepository()
        # D5: the Player row is the aggregate-root mutex for the
        # ``owned = cargo + equipped`` invariant.  ``_lock_player`` uses this
        # repo's ``get_by_id_for_update`` to acquire it.
        self.player_repo = player_repo if player_repo is not None else PlayerRepository()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _lock_player(self, db: AsyncSession, player_id: int) -> None:
        """Acquire the aggregate-root ``SELECT ... FOR UPDATE`` lock on the Player row (D5).

        The Player row (``players.id = player_id``) is the aggregate-root mutex
        for the whole ``owned = quantity(cargo) + Σ equipped`` invariant, which
        spans the ``Player`` row plus N ``player_ships`` JSON-slot lists plus M
        ``player_inventories`` rows — all keyed by ``player_id``.  Because
        ``FOR UPDATE`` is self-conflicting, taking this lock FIRST in every
        same-player loadout/inventory mutation serialises it against every other
        same-player mutation (loadout *and* credit, since the credit invariant
        already locks the same row), while leaving other players unaffected
        (a different row).

        This MUST be called at the top of each public mutation method, before any
        read whose value feeds a read-modify-write (slot caps, cargo quantity,
        slot lists), so the lock-holder always re-reads committed state.  The lock
        auto-releases at transaction end / rollback (PostgreSQL row-level locks),
        so it is scoped exactly to the caller's ``db.begin()`` unit of work.

        ``get_by_id_for_update`` uses ``populate_existing=True``; if the player
        was pre-loaded unlocked earlier in the transaction, the locked re-fetch
        overwrites the stale identity-mapped attributes with committed state.

        This is a pure lock primitive: it intentionally does NOT raise when no
        Player row exists (validation of player existence is the caller's job).
        """
        await self.player_repo.get_by_id_for_update(db, player_id)

    async def _resolve_concrete_type(self, db: AsyncSession, item_name: str, fallback_kind: str | None = None) -> str:
        """Resolve an item name to its concrete inventory item_type.

        Order: STI discriminator → equipment-kind fallback → "module".
        """
        base = await self.item_repo.get_by_name_any_type(db, item_name)
        if base is not None:
            concrete = item_discriminator_to_concrete_type(base.type)
            if concrete:
                return concrete
        if fallback_kind is not None:
            return _INVENTORY_TYPE_MAP.get(fallback_kind, "module")
        return "module"

    async def _get_static_ship_caps(self, db: AsyncSession, ship: PlayerShip) -> dict[str, int]:
        """Return the static ship's per-kind max-slot caps."""
        static = await self.ship_repo.get_by_name(db, ship.ship_name)
        if static is None:
            raise ValueError(f"Static ship data not found for '{ship.ship_name}'")
        return {
            "weapons": static.max_primaries,
            "modules": static.max_modules,
            "turrets": static.max_turrets,
            "secondary_weapons": getattr(static, "max_secondaries", 0) or 0,
        }

    @staticmethod
    def _get_slot(ship: PlayerShip, kind: str) -> list[str]:
        """Return a copy of the ship's slot list for the given kind.

        G.4: None entries in the JSON list (from corrupt legacy data) are silently
        filtered out with a WARNING so downstream callers never encounter None where
        a string item name is expected (prevents ``_resolve_concrete_type(db, None)``
        from triggering an unexpected DB lookup or error).
        """
        attr_map = {
            "weapons": ship.weapons,
            "modules": ship.modules,
            "turrets": ship.turrets,
            "secondary_weapons": getattr(ship, "secondary_weapons", None),
        }
        raw = attr_map.get(kind)
        if not raw:
            return []
        filtered = [x for x in raw if x is not None]
        if len(filtered) < len(raw):
            flogger.warning(
                "G.4: filtered %d None entry(ies) from player_ship %s '%s' slot list — corrupt legacy data",
                len(raw) - len(filtered),
                getattr(ship, "id", "?"),
                kind,
            )
        return filtered

    @staticmethod
    def _set_slot(ship: PlayerShip, kind: str, items: list[str]) -> None:
        """Assign a new slot list to the ship (in-place ORM mutation)."""
        if kind == "weapons":
            ship.weapons = list(items)
        elif kind == "modules":
            ship.modules = list(items)
        elif kind == "turrets":
            ship.turrets = list(items)
        elif kind == "secondary_weapons":
            ship.secondary_weapons = list(items)
        else:  # pragma: no cover — guarded by callers
            raise ValueError(f"Invalid slot kind: {kind}")

    async def _remove_one_slot_reference_from_other_ships(
        self, db: AsyncSession, *, player_id: int, exclude_ship_id: int, kind: str, item_name: str
    ) -> int:
        """Remove ONE occurrence of ``item_name`` from any of the player's
        OTHER ships' ``kind`` slot list.

        Used by the anti-duplication guard in
        :meth:`evacuate_ship_loadout_to_inventory` to silently drop legacy
        duplicates so a single equipped instance does not get materialised
        twice into inventory rows on repeated evacuations.

        Returns the number of slot references removed (0 or 1).
        """
        all_ships = await self.player_ship_repo.get_player_ships(db, player_id)
        for other in all_ships:
            if other.id == exclude_ship_id:
                continue
            current = self._get_slot(other, kind)
            if item_name in current:
                current.remove(item_name)
                self._set_slot(other, kind, current)
                # CI-16 (R3): also remove any orphaned ammo from the other ship's sidecar
                # (the duplicate slot is being silently dropped; its ammo must not linger)
                if kind == "secondary_weapons":
                    other_ammo = dict(getattr(other, "secondary_ammo", None) or {})
                    if item_name in other_ammo:
                        del other_ammo[item_name]
                        other.secondary_ammo = other_ammo  # reassign — never mutate in place
                flogger.warning(
                    "B.19 anti-duplication guard: removed phantom %s '%s' from player_ship %d "
                    "(player %d, kept on ship %d)",
                    kind,
                    item_name,
                    other.id,
                    player_id,
                    exclude_ship_id,
                )
                return 1
        return 0

    async def _validate_module_equip_limit(self, db: AsyncSession, ship: PlayerShip, item_name: str) -> None:
        """Enforce ``GameConstants.MODULE_EQUIP_LIMITS`` for the module item.

        Raises ``ValueError`` if the unique module class limit is reached.
        """
        base = await self.item_repo.get_by_name_any_type(db, item_name)
        if base is None:
            return  # already validated as existing — skip gracefully

        module_class = base.type
        limit = GameConstants.MODULE_EQUIP_LIMITS.get(module_class)
        if limit is None:
            return
        if limit == 0:
            raise ValueError(f"Module class '{module_class}' cannot be equipped (limit=0)")
        if limit < 0:
            return  # unlimited

        equipped_modules = self._get_slot(ship, "modules")
        for equipped_name in equipped_modules:
            equipped_item = await self.item_repo.get_by_name_any_type(db, equipped_name)
            if equipped_item is not None and equipped_item.type == module_class:
                raise ValueError(
                    f"Cannot equip '{item_name}': module class '{module_class}' is limited to "
                    f"{limit} equipped at once. Already equipped: '{equipped_name}'"
                )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @requires_transaction
    async def equip_one(
        self,
        db: AsyncSession,
        *,
        player_id: int,
        ship_id: int,
        item_name: str,
        equipment_type: str | None = None,
    ) -> dict[str, Any]:
        """Atomic equip operation.

        Validates ownership, slot availability, MODULE_EQUIP_LIMITS, decrements
        the inventory row, and appends to the ship's slot list.  Caller owns
        the transaction (this method never commits).

        Raises:
            ValueError: validation failure (mapped to HTTP 400).
            InvalidItemTypeError: secondary_weapons gated off.
            RuntimeError: invoked outside an active transaction (AC-6 guard).
        """
        # D5: aggregate-root lock FIRST — before any read that feeds the
        # cargo/slot read-modify-write below (serialises same-player equips).
        await self._lock_player(db, player_id)

        # 1. Resolve / validate equipment_type
        if equipment_type is None:
            base = await self.item_repo.get_by_name_any_type(db, item_name)
            if base is None:
                raise ValueError(f"Item '{item_name}' not found in game data")
            category = _item_type_to_equipment_category(base.type)
            if category is None:
                raise ValueError(f"Item '{item_name}' (type={base.type!r}) is not equippable")
            equipment_type = category
        elif equipment_type not in VALID_EQUIPMENT_TYPES:
            raise ValueError(
                f"Invalid equipment_type '{equipment_type}'. Must be one of: {sorted(VALID_EQUIPMENT_TYPES)}"
            )

        # Defense-in-depth: secondary_weapon surface gate.
        if equipment_type == "secondary_weapons" and ("secondary_weapon" not in GameConstants.CURRENTLY_ENABLED_TYPES):
            raise InvalidItemTypeError("Secondary weapons are not currently enabled")

        # 2. Ship exists and belongs to player.  B.15: DB errors during ship
        # lookup surface as friendly ValueError (HTTP 400) rather than raw 500.
        try:
            ship = await self.player_ship_repo.get_by_id(db, ship_id)
        except Exception as exc:
            flogger.error("DB error fetching ship_id=%d: %s", ship_id, exc)
            raise ValueError(f"Ship with ID {ship_id} could not be retrieved.") from exc
        if ship is None:
            raise ValueError(f"Ship {ship_id} not found")
        if ship.player_id != player_id:
            raise ValueError(f"Ship {ship_id} does not belong to player {player_id}")

        # 3. Resolve concrete inventory type and validate item exists in game data
        inventory_type = _INVENTORY_TYPE_MAP[equipment_type]
        game_item = await self.item_repo.get_by_name(db, item_name, item_type=inventory_type)
        if game_item is None:
            raise ValueError(f"Item '{item_name}' (type={inventory_type}) not found in game data")

        # 4. Player owns item in inventory
        inv_item = await self.inventory_repo.get_player_item(db, player_id, inventory_type, item_name)
        if inv_item is None:
            raise ValueError(f"Item '{item_name}' (type={inventory_type}) not found in your inventory")

        # 5. Slot availability against static ship caps — resolved first so we can
        # gate the B.41 guard on whether a free slot exists.
        caps = await self._get_static_ship_caps(db, ship)
        current_slot = self._get_slot(ship, equipment_type)

        # CI-16 (BUG-3 fix): secondary_weapons top-up path MUST come BEFORE the slot-cap
        # guard.  A player with both secondary slots full who re-equips an already-equipped
        # type should get a top-up, not a "No available slots" error.  The slot-cap guard
        # only applies when equipping a NEW (not-yet-equipped) weapon type.
        if equipment_type == "secondary_weapons" and item_name in current_slot:
            # Top-up: all cargo copies → ammo stack (no slot mutation needed)
            cargo_qty = inv_item.quantity
            if cargo_qty > 0:
                await self.inventory_repo.remove_item(
                    db, player_id, inventory_type, item_name, quantity=cargo_qty, commit=False
                )
                current_ammo: dict = dict(getattr(ship, "secondary_ammo", None) or {})
                current_ammo[item_name] = current_ammo.get(item_name, 0) + cargo_qty
                ship.secondary_ammo = current_ammo  # reassign — never mutate in place
                await db.flush()
                flogger.info(
                    "Player %d topped up secondary '%s' on ship %d: +%d rounds (ammo now %d)",
                    player_id,
                    item_name,
                    ship_id,
                    cargo_qty,
                    current_ammo[item_name],
                )
            ship = await self.player_ship_repo.get_by_id(db, ship_id)
            return {
                "success": True,
                "ship": ship,
                "message": f"Topped up '{item_name}' ammo on ship {ship_id}",
                "equipment_type": equipment_type,
            }

        # 4b. B.41 — guard against equipping when no cargo copies remain.
        # Only runs when there IS a free slot available.  When slots are full, the
        # slot-full path below fires instead, and the swap flow (unequip-then-equip)
        # is handled by the caller (cog / EquipmentService).  After the unequip step
        # the inventory quantity will have increased by one, so the guard will
        # pass correctly on the subsequent equip call.
        #
        # INVARIANT: player_inventories.quantity is CARGO-ONLY — it does NOT count
        # equipped copies.  Equipped copies live solely in player_ships JSON slots.
        # Total owned = quantity(cargo) + sum(equipped across all ships).
        #
        # The correct guard is therefore: block if quantity <= 0 (no cargo copies
        # available to consume).  The previous condition (already_equipped >= quantity)
        # was wrong: with 1 equipped + 1 in cargo, already_equipped(1) >= quantity(1)
        # = True, which incorrectly blocked a legitimate equip of the cargo copy.
        if len(current_slot) < caps[equipment_type] and inv_item.quantity <= 0:
            raise ValueError(f"No unequipped copies remain: '{item_name}' has {inv_item.quantity} in cargo.")

        if len(current_slot) >= caps[equipment_type]:
            raise ValueError(
                f"No available {equipment_type} slots on ship '{ship.ship_name}' "
                f"({len(current_slot)}/{caps[equipment_type]} slots used)"
            )

        # 6. MODULE_EQUIP_LIMITS enforcement
        if equipment_type == "modules":
            await self._validate_module_equip_limit(db, ship, item_name)

        # 7. Decrement inventory (commit=False)
        # For secondary_weapons: decrement the WHOLE cargo stack (all rounds move to ammo sidecar)
        if equipment_type == "secondary_weapons":
            cargo_qty_to_move = inv_item.quantity
            await self.inventory_repo.remove_item(
                db, player_id, inventory_type, item_name, quantity=cargo_qty_to_move, commit=False
            )
        else:
            cargo_qty_to_move = 1
            await self.inventory_repo.remove_item(db, player_id, inventory_type, item_name, quantity=1, commit=False)

        # 8. Append to ship slot (commit=False)
        await self.player_ship_repo.add_equipment(db, ship_id, equipment_type, item_name, commit=False)

        # CI-16: for secondary_weapons, seed the ammo sidecar with the moved cargo quantity
        if equipment_type == "secondary_weapons":
            # Re-fetch ship to get the current secondary_ammo after add_equipment flush
            ship = await self.player_ship_repo.get_by_id(db, ship_id)
            current_ammo_new: dict = dict(getattr(ship, "secondary_ammo", None) or {})
            current_ammo_new[item_name] = current_ammo_new.get(item_name, 0) + cargo_qty_to_move
            ship.secondary_ammo = current_ammo_new  # reassign — never mutate in place
            await db.flush()

        flogger.info(
            "Player %d equipped '%s' (%s) on ship %d via consistency service",
            player_id,
            item_name,
            equipment_type,
            ship_id,
        )
        # Re-fetch ship for fresh slot lists in the response
        ship = await self.player_ship_repo.get_by_id(db, ship_id)
        return {
            "success": True,
            "ship": ship,
            "message": f"Successfully equipped '{item_name}' on ship {ship_id}",
            "equipment_type": equipment_type,
        }

    @requires_transaction
    async def unequip_one(
        self,
        db: AsyncSession,
        *,
        player_id: int,
        ship_id: int,
        item_name: str,
        equipment_type: str | None = None,
    ) -> dict[str, Any]:
        """Atomic unequip operation.

        Removes from ship slot, increments inventory row.  Caller owns the
        transaction (this method never commits).

        Raises:
            ValueError: validation failure (mapped to HTTP 400).
            RuntimeError: invoked outside an active transaction (AC-6 guard).
        """
        # D5: aggregate-root lock FIRST — before the slot-read / cargo-write RMW.
        await self._lock_player(db, player_id)

        # Ship first so we can do fallback scan if equipment_type can't be auto-detected.
        # B.15: DB errors during ship lookup surface as friendly ValueError.
        try:
            ship = await self.player_ship_repo.get_by_id(db, ship_id)
        except Exception as exc:
            flogger.error("DB error fetching ship_id=%d: %s", ship_id, exc)
            raise ValueError(f"Ship with ID {ship_id} could not be retrieved.") from exc
        if ship is None:
            raise ValueError(f"Ship {ship_id} not found")
        if ship.player_id != player_id:
            raise ValueError(f"Ship {ship_id} does not belong to player {player_id}")

        # Resolve equipment_type
        if equipment_type is None:
            base = await self.item_repo.get_by_name_any_type(db, item_name)
            category: str | None = None
            if base is not None:
                category = _item_type_to_equipment_category(base.type)
            if category is None:
                # Fallback: scan equipped slots for the item
                for kind in _SLOT_KINDS:
                    if item_name in self._get_slot(ship, kind):
                        category = kind
                        break
            if category is None:
                raise ValueError(f"Item '{item_name}' not found in any equipped slot on ship {ship_id}")
            equipment_type = category
        elif equipment_type not in VALID_EQUIPMENT_TYPES:
            raise ValueError(
                f"Invalid equipment_type '{equipment_type}'. Must be one of: {sorted(VALID_EQUIPMENT_TYPES)}"
            )

        # Item is currently equipped on ship
        equipped = self._get_slot(ship, equipment_type)
        if item_name not in equipped:
            raise ValueError(f"Item '{item_name}' is not equipped in {equipment_type} on ship {ship_id}")

        # CI-16: for secondary_weapons, read remaining ammo BEFORE removing from slot
        # (we need to know how many rounds to return to cargo)
        rounds_to_return: int = 1  # default for primaries/turrets/modules
        if equipment_type == "secondary_weapons":
            current_ammo_unequip: dict = dict(getattr(ship, "secondary_ammo", None) or {})
            rounds_to_return = current_ammo_unequip.pop(item_name, 0)
            # Reassign sidecar with key removed (never mutate in place)
            ship.secondary_ammo = current_ammo_unequip
            await db.flush()

        # Remove from ship slot (commit=False)
        await self.player_ship_repo.remove_equipment(db, ship_id, equipment_type, item_name, commit=False)

        # Add to inventory using concrete type (commit=False)
        # For secondary_weapons: return the WHOLE remaining ammo stack (rounds_to_return rounds)
        inventory_type = await self._resolve_concrete_type(db, item_name, fallback_kind=equipment_type)
        qty_to_cargo = rounds_to_return if equipment_type == "secondary_weapons" else 1
        if qty_to_cargo > 0:
            await self.inventory_repo.add_item(
                db, player_id, inventory_type, item_name, quantity=qty_to_cargo, commit=False
            )

        flogger.info(
            "Player %d unequipped '%s' (%s) from ship %d via consistency service (returned %d to cargo)",
            player_id,
            item_name,
            equipment_type,
            ship_id,
            qty_to_cargo,
        )
        ship = await self.player_ship_repo.get_by_id(db, ship_id)
        return {
            "success": True,
            "ship": ship,
            "message": f"Successfully unequipped '{item_name}' from ship {ship_id}",
            "equipment_type": equipment_type,
        }

    @requires_transaction
    async def transfer_loadout_to_new_ship(
        self,
        db: AsyncSession,
        *,
        player_id: int,
        src_ship: PlayerShip | None,
        dst_ship: PlayerShip,
        slot_limits: dict[str, int],
    ) -> dict[str, Any]:
        """Move src_ship's loadout to dst_ship; overflow goes to inventory.

        Handles non-empty destination ships via **merge-with-overflow** (B.95):
        existing items on ``dst_ship`` are kept in their slots first; then
        items from ``src_ship`` are merged in up to the remaining free slots.
        Any items from ``src_ship`` that cannot fit overflow to inventory.

        After this call:
        - ``dst_ship.<kind>`` contains up to ``slot_limits[kind]`` items
          (existing dst items kept first, src items filled into remaining slots).
        - ``src_ship.<kind>`` is cleared.
        - Inventory is incremented for every overflow item from src.

        Net effect: each item-name appears in exactly one new place.

        Returns ``{transferred, overflowed, breakdown}``.  When ``src_ship``
        is None (player has no prior ship), returns zero counts.
        """
        breakdown: dict[str, dict[str, list[str]]] = {
            kind: {"transferred": [], "overflowed": []} for kind in _SLOT_KINDS
        }
        if src_ship is None:
            return {"transferred": 0, "overflowed": 0, "breakdown": breakdown}

        # CI-16 (R1): read secondary_ammo from src before slot mutations
        src_ammo: dict[str, int] = dict(getattr(src_ship, "secondary_ammo", None) or {})
        dst_ammo: dict[str, int] = dict(getattr(dst_ship, "secondary_ammo", None) or {})

        for kind in _SLOT_KINDS:
            src_items = self._get_slot(src_ship, kind)
            dst_existing = self._get_slot(dst_ship, kind)
            cap = slot_limits.get(kind, 0) or 0

            # B.95 merge-with-overflow: dst items occupy slots first.
            # Remaining free slots are filled from src; excess src items overflow.
            free_slots = max(0, cap - len(dst_existing))
            fitting_from_src = src_items[:free_slots]
            overflow_from_src = src_items[free_slots:]

            # Push overflow to inventory (concrete type via STI discriminator)
            for name in overflow_from_src:
                concrete = await self._resolve_concrete_type(db, name, fallback_kind=kind)
                if kind == "secondary_weapons":
                    # CI-16 (R1): overflow → cargo: return WHOLE ammo stack, not 1 copy.
                    # BUG-2 fix: use plain rounds (not max(1, rounds)) — a depleted (0-round)
                    # secondary must return 0 copies to cargo, not invent a round.
                    rounds = src_ammo.pop(name, 0)
                    if rounds > 0:
                        await self.inventory_repo.add_item(db, player_id, concrete, name, rounds, commit=False)
                else:
                    await self.inventory_repo.add_item(db, player_id, concrete, name, 1, commit=False)

            # Merge src fitting items into dst, clear src
            self._set_slot(dst_ship, kind, dst_existing + fitting_from_src)
            self._set_slot(src_ship, kind, [])

            # CI-16 (R1): for fitting secondaries, move ammo src→dst (add to existing dst ammo)
            if kind == "secondary_weapons":
                for name in fitting_from_src:
                    rounds = src_ammo.pop(name, 0)
                    dst_ammo[name] = dst_ammo.get(name, 0) + rounds

            breakdown[kind]["transferred"] = list(fitting_from_src)
            breakdown[kind]["overflowed"] = list(overflow_from_src)

        # CI-16 (R1): reassign secondary_ammo on both ships (never mutate in place)
        src_ship.secondary_ammo = src_ammo
        dst_ship.secondary_ammo = dst_ammo

        await db.flush()

        transferred = sum(len(v["transferred"]) for v in breakdown.values())
        overflowed = sum(len(v["overflowed"]) for v in breakdown.values())
        flogger.info(
            "Player %d loadout transferred from ship %d to ship %d: %d fitted, %d overflow",
            player_id,
            src_ship.id,
            dst_ship.id,
            transferred,
            overflowed,
        )
        return {"transferred": transferred, "overflowed": overflowed, "breakdown": breakdown}

    @requires_transaction
    async def evacuate_ship_loadout_to_inventory(
        self,
        db: AsyncSession,
        *,
        ship: PlayerShip,
    ) -> dict[str, Any]:
        """Move all equipped items from a ship's slot lists to inventory.

        Used by ``sell_ship clear_equipment=True``, ``transfer_ship``, and
        ``admin_remove_ship``.  Operates only on items legitimately referenced
        on this ship; clears the ship's slot lists as part of the same
        transaction.  Idempotent: a second call on the same ship produces no
        items.

        Anti-duplication guard (B.19 exploit closure): before adding to
        inventory, scan the player's other ships for a duplicate slot
        reference; if found, remove it from the *other* ship and skip the
        inventory mint for that item ("losing" copy silently dropped).  This
        prevents legacy phantom-item duplicates from being materialised twice.

        Returns ``{items_returned: list[str], items_returned_detail: dict[kind, list[str]],
        duplicates_dropped: int}``.
        """
        # D5: aggregate-root lock FIRST (keyed by the ship's owner) — before the
        # anti-dup scan / cargo-mint RMW across this and the player's other ships.
        await self._lock_player(db, ship.player_id)

        items_returned: list[str] = []
        items_returned_detail: dict[str, list[str]] = {kind: [] for kind in _SLOT_KINDS}
        duplicates_dropped = 0

        # CI-16 (R2): read secondary_ammo before clearing slots
        ship_ammo: dict[str, int] = dict(getattr(ship, "secondary_ammo", None) or {})

        for kind in _SLOT_KINDS:
            equipped = self._get_slot(ship, kind)
            for name in equipped:
                # Anti-duplication guard: drop the duplicate, do not mint
                removed_other = await self._remove_one_slot_reference_from_other_ships(
                    db,
                    player_id=ship.player_id,
                    exclude_ship_id=ship.id,
                    kind=kind,
                    item_name=name,
                )
                if removed_other:
                    # The duplicate copy on the other ship was deleted; this side
                    # remains as the "winning" copy and proceeds to inventory.
                    duplicates_dropped += removed_other
                # Add the legitimate copy to inventory
                concrete = await self._resolve_concrete_type(db, name, fallback_kind=kind)
                if kind == "secondary_weapons":
                    # CI-16 (R2): return WHOLE remaining ammo stack, not 1 copy.
                    # BUG-2 fix: use plain rounds (not max(1, rounds)) — a depleted (0-round)
                    # secondary must return 0 copies to cargo, not invent a round.
                    rounds = ship_ammo.pop(name, 0)
                    if rounds > 0:
                        await self.inventory_repo.add_item(db, ship.player_id, concrete, name, rounds, commit=False)
                else:
                    await self.inventory_repo.add_item(db, ship.player_id, concrete, name, 1, commit=False)
                items_returned.append(name)
                items_returned_detail[kind].append(name)
            # Clear this ship's slot
            self._set_slot(ship, kind, [])

        # CI-16 (R2): clear secondary_ammo sidecar (reassign — never mutate in place)
        ship.secondary_ammo = {}

        await db.flush()

        flogger.info(
            "Evacuated ship %d (player %d): %d items moved to inventory, %d legacy duplicates dropped",
            ship.id,
            ship.player_id,
            len(items_returned),
            duplicates_dropped,
        )
        return {
            "items_returned": items_returned,
            "items_returned_detail": items_returned_detail,
            "duplicates_dropped": duplicates_dropped,
        }

    @requires_transaction
    async def reconcile_active_ship_slots(
        self,
        db: AsyncSession,
        *,
        player_id: int,
        target_ship_id: int,
    ) -> dict[str, Any]:
        """Reconcile target ship's loadout against its static slot caps.

        Used by ``set_active_ship`` to evacuate overflow to inventory when
        switching to a smaller ship.  Returns a structured report so the
        cog can render a "X items moved to cargo" notice.
        """
        # D5: aggregate-root lock FIRST — before the slot-cap read / overflow-mint RMW.
        await self._lock_player(db, player_id)

        ship = await self.player_ship_repo.get_by_id(db, target_ship_id)
        if ship is None:
            raise ValueError(f"Ship {target_ship_id} not found")
        if ship.player_id != player_id:
            raise ValueError(f"Ship {target_ship_id} does not belong to player {player_id}")

        caps = await self._get_static_ship_caps(db, ship)

        evacuated: dict[str, list[str]] = {kind: [] for kind in _SLOT_KINDS}
        any_evacuated = False

        # CI-16 (BUG-1 fix): read secondary_ammo before slot mutations so we can return
        # the correct round count (not hard-coded 1) for any overflowing secondary weapon.
        ship_ammo: dict[str, int] = dict(getattr(ship, "secondary_ammo", None) or {})
        ammo_dirty = False

        for kind in _SLOT_KINDS:
            current = self._get_slot(ship, kind)
            cap = caps[kind]
            if len(current) > cap:
                keep = current[:cap]
                overflow = current[cap:]
                self._set_slot(ship, kind, keep)
                for name in overflow:
                    concrete = await self._resolve_concrete_type(db, name, fallback_kind=kind)
                    if kind == "secondary_weapons":
                        # BUG-1 fix: return the WHOLE ammo stack, not 1 round.
                        # BUG-2 guard: skip add_item for depleted (0-round) secondaries.
                        rounds = ship_ammo.pop(name, 0)
                        ammo_dirty = True
                        if rounds > 0:
                            await self.inventory_repo.add_item(db, player_id, concrete, name, rounds, commit=False)
                    else:
                        await self.inventory_repo.add_item(db, player_id, concrete, name, 1, commit=False)
                evacuated[kind] = list(overflow)
                any_evacuated = True

        # CI-16 (BUG-1 fix): reassign ammo sidecar if any overflow secondaries were popped
        if ammo_dirty:
            ship.secondary_ammo = ship_ammo  # reassign — never mutate in place

        if any_evacuated:
            await db.flush()
            total_evacuated = sum(len(v) for v in evacuated.values())
            flogger.info(
                "Reconciled active ship %d for player %d: %d items overflowed to inventory",
                target_ship_id,
                player_id,
                total_evacuated,
            )

        return {"evacuated_items": evacuated, "any_evacuated": any_evacuated}

    @requires_transaction
    async def activate_ship(
        self,
        db: AsyncSession,
        *,
        player_id: int,
        target_ship_id: int,
        player_repo: Any,
    ) -> dict[str, Any]:
        """Canonical ship activation choke-point (B.94 / B.95).

        Performs the full activation sequence atomically:

        1. Look up the player's currently-active ship (if any).
        2. Reconcile the *target* ship's loadout against its static slot caps
           (invariant I4) — overflows go to inventory.
        3. Transfer the loadout from the currently-active ship to the target ship
           using merge-with-overflow semantics (B.95).
        4. Flip ``is_active`` flags via ``PlayerShipRepository.set_active_ship``.
        5. Update ``Player.active_ship_id`` via ``player_repo.update_active_ship``.

        All writes use ``commit=False``; the **caller** owns the transaction
        (router-level ``async with db.begin()`` or shop-service caller block).

        Args:
            player_id: ID of the owning player.
            target_ship_id: ID of the ``PlayerShip`` to activate.
            player_repo: An instance of ``PlayerRepository`` — passed in so the
                caller can share its already-mocked repository in tests.

        Returns:
            A dict with keys:
            - ``ship``            — the activated ``PlayerShip`` ORM instance.
            - ``evacuated_items`` — slot-keyed dict of items overflowed from the
                                    target ship during reconciliation.
            - ``any_evacuated``   — bool, True if any reconciliation overflow occurred.
            - ``transferred``     — total items transferred from the old active ship.
            - ``overflowed``      — total items from the old active ship that could
                                    not fit and went to inventory instead.
            - ``transfer_breakdown`` — per-slot breakdown from the transfer step.

        Raises:
            ValueError: ship not found, or ship does not belong to player.
            RuntimeError: invoked outside an active transaction (I3 guard).
        """
        # D5: aggregate-root lock FIRST — before reconcile / transfer / set-active.
        # The nested reconcile_active_ship_slots / transfer_loadout_to_new_ship
        # calls re-acquire the same Player row's FOR UPDATE within this same
        # transaction, which is a documented no-op (a transaction may re-hold its
        # own row lock).
        await self._lock_player(db, player_id)

        # 1. Fetch the target ship and validate ownership.
        target_ship = await self.player_ship_repo.get_by_id(db, target_ship_id)
        if target_ship is None:
            raise ValueError(f"Ship {target_ship_id} not found")
        if target_ship.player_id != player_id:
            raise ValueError(f"Ship {target_ship_id} does not belong to player {player_id}")

        # 2. Reconcile target ship's slots against its static caps (I4).
        reconcile = await self.reconcile_active_ship_slots(db, player_id=player_id, target_ship_id=target_ship_id)
        # Re-fetch after reconcile (slot mutation may have occurred).
        target_ship = await self.player_ship_repo.get_by_id(db, target_ship_id)

        # 3. Get currently-active ship (may be None, or may already be the target).
        current_active = await self.player_ship_repo.get_active_ship(db, player_id)

        transfer_result: dict[str, Any] = {"transferred": 0, "overflowed": 0, "breakdown": {}}
        if current_active is not None and current_active.id != target_ship_id:
            # Build slot limits for the target ship from static data.
            slot_limits = await self._get_static_ship_caps(db, target_ship)
            transfer_result = await self.transfer_loadout_to_new_ship(
                db,
                player_id=player_id,
                src_ship=current_active,
                dst_ship=target_ship,
                slot_limits=slot_limits,
            )

        # 4. Flip is_active flags atomically.
        activated_ship = await self.player_ship_repo.set_active_ship(db, player_id, target_ship_id, commit=False)

        # 5. Update Player.active_ship_id.
        await player_repo.update_active_ship(db, player_id, target_ship_id, commit=False)

        flogger.info(
            "Player %d: ship %d (%s) activated via choke-point; transferred=%d overflowed=%d reconcile_evacuated=%s",
            player_id,
            target_ship_id,
            target_ship.ship_name,
            transfer_result["transferred"],
            transfer_result["overflowed"],
            reconcile["any_evacuated"],
        )
        return {
            "ship": activated_ship,
            "evacuated_items": reconcile["evacuated_items"],
            "any_evacuated": reconcile["any_evacuated"],
            "transferred": transfer_result["transferred"],
            "overflowed": transfer_result["overflowed"],
            "transfer_breakdown": transfer_result.get("breakdown", {}),
        }

    @requires_transaction
    async def repair_player(
        self,
        db: AsyncSession,
        player_id: int,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Detect and remove duplicate slot references across a player's ships.

        Used by the B.19 data-fixup migration AND exposed for admin tooling.
        Tie-breaking: the active ship's references win; non-active ships
        ordered by id ascending.

        Phantom items (no inventory provenance) are deliberately preserved on
        their first appearance — see § Phantom-starter handling in the design
        spec.

        Returns ``{duplicates_removed, ships_modified, ships_scanned, dry_run}``.
        """
        # D5: aggregate-root lock FIRST — before scanning/rewriting slot lists.
        await self._lock_player(db, player_id)

        # Use the repository method (which sorts by is_active desc, created_at)
        # rather than a raw select — keeps the test path mockable and matches
        # the "active wins" tie-breaking rule.
        ships = await self.player_ship_repo.get_player_ships(db, player_id)
        # Re-sort: active first, then by id ascending (per design spec).
        ships = sorted(ships, key=lambda s: (not bool(s.is_active), s.id))

        seen: dict[tuple[str, str], int] = {}  # (item_name, kind) -> winning ship_id
        duplicates_removed = 0
        ships_modified: set[int] = set()

        for ship in ships:
            # CI-16 (repair_player): track ammo mutations per ship for secondary_weapons
            ship_ammo_dirty = False
            ship_ammo: dict[str, int] = dict(getattr(ship, "secondary_ammo", None) or {})

            for kind in _SLOT_KINDS:
                current = self._get_slot(ship, kind)
                cleaned: list[str] = []
                for name in current:
                    key = (name, kind)
                    if key not in seen:
                        seen[key] = ship.id
                        cleaned.append(name)
                    else:
                        # Duplicate — drop this reference; do NOT mint inventory
                        duplicates_removed += 1
                        ships_modified.add(ship.id)
                        flogger.warning(
                            "B.19 repair: removed duplicate %s '%s' from player_ship %d (kept on player_ship %d)",
                            kind,
                            name,
                            ship.id,
                            seen[key],
                        )
                        # CI-16: also remove orphaned ammo for dropped secondary duplicate
                        if kind == "secondary_weapons" and name in ship_ammo:
                            del ship_ammo[name]
                            ship_ammo_dirty = True
                if len(cleaned) != len(current) and not dry_run:
                    self._set_slot(ship, kind, cleaned)

            # CI-16: reassign secondary_ammo if we removed any orphaned entries
            if ship_ammo_dirty and not dry_run:
                ship.secondary_ammo = ship_ammo

        if not dry_run and ships_modified:
            await db.flush()

        # G.3: Post-condition check — after a live repair, re-scan to assert that
        # zero duplicates remain.  Wrapped in ``if __debug__:`` so it only runs in
        # normal Python mode (not -O/PYTHONOPTIMIZE).  If a bug in ``_set_slot``
        # prevented the ORM mutation from being flushed, this will surface it as a
        # WARNING rather than silently completing a corrupt migration.
        if __debug__ and not dry_run and ships_modified:
            post_seen: dict[tuple[str, str], int] = {}
            residual_duplicates = 0
            for ship in ships:
                for kind in _SLOT_KINDS:
                    for name in self._get_slot(ship, kind):
                        key = (name, kind)
                        if key in post_seen:
                            residual_duplicates += 1
                        else:
                            post_seen[key] = ship.id
            if residual_duplicates > 0:
                flogger.warning(
                    "G.3 repair_player post-condition FAILED for player %d: "
                    "%d duplicate slot reference(s) remain after flush — "
                    "_set_slot mutation may not have been applied correctly.",
                    player_id,
                    residual_duplicates,
                )
            else:
                flogger.debug(
                    "G.3 repair_player post-condition OK for player %d: zero duplicate slot references after flush.",
                    player_id,
                )

        return {
            "player_id": player_id,
            "ships_scanned": len(ships),
            "ships_modified": len(ships_modified),
            "duplicates_removed": duplicates_removed,
            "dry_run": dry_run,
        }


# Re-export _SLOT_MAP for callers that need static slot field name (e.g. admin diagnostics)
__all__ = [
    "_SLOT_KINDS",
    "_SLOT_MAP",
    "LoadoutConsistencyService",
]
