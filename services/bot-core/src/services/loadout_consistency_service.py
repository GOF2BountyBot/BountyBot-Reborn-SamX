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

See ``/proj/recon/B19-design.md`` for the full architectural rationale.
"""

from typing import Any

from persist.models.player_ship import PlayerShip
from persist.repositories.inventory_repository import InventoryRepository
from persist.repositories.item_repository import ItemRepository
from persist.repositories.player_ship_repository import PlayerShipRepository
from persist.repositories.ship_repository import ShipRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

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
    ) -> None:
        # Optional constructor injection so callers (e.g.
        # ``EquipmentService.equip_item``) can share their already-mocked
        # repositories with the consistency service in unit tests.
        self.player_ship_repo = player_ship_repo if player_ship_repo is not None else PlayerShipRepository()
        self.inventory_repo = inventory_repo if inventory_repo is not None else InventoryRepository()
        self.item_repo = item_repo if item_repo is not None else ItemRepository()
        self.ship_repo = ship_repo if ship_repo is not None else ShipRepository()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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
        """Return a copy of the ship's slot list for the given kind."""
        attr_map = {
            "weapons": ship.weapons,
            "modules": ship.modules,
            "turrets": ship.turrets,
            "secondary_weapons": getattr(ship, "secondary_weapons", None),
        }
        raw = attr_map.get(kind)
        return list(raw) if raw else []

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
        """
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

        # 5. Slot availability against static ship caps
        caps = await self._get_static_ship_caps(db, ship)
        current_slot = self._get_slot(ship, equipment_type)
        if len(current_slot) >= caps[equipment_type]:
            raise ValueError(
                f"No available {equipment_type} slots on ship '{ship.ship_name}' "
                f"({len(current_slot)}/{caps[equipment_type]} slots used)"
            )

        # 6. MODULE_EQUIP_LIMITS enforcement
        if equipment_type == "modules":
            await self._validate_module_equip_limit(db, ship, item_name)

        # 7. Decrement inventory (commit=False)
        await self.inventory_repo.remove_item(db, player_id, inventory_type, item_name, quantity=1, commit=False)

        # 8. Append to ship slot (commit=False)
        await self.player_ship_repo.add_equipment(db, ship_id, equipment_type, item_name, commit=False)

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
        """
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

        # Remove from ship slot (commit=False)
        await self.player_ship_repo.remove_equipment(db, ship_id, equipment_type, item_name, commit=False)

        # Add to inventory using concrete type (commit=False)
        inventory_type = await self._resolve_concrete_type(db, item_name, fallback_kind=equipment_type)
        await self.inventory_repo.add_item(db, player_id, inventory_type, item_name, quantity=1, commit=False)

        flogger.info(
            "Player %d unequipped '%s' (%s) from ship %d via consistency service",
            player_id,
            item_name,
            equipment_type,
            ship_id,
        )
        ship = await self.player_ship_repo.get_by_id(db, ship_id)
        return {
            "success": True,
            "ship": ship,
            "message": f"Successfully unequipped '{item_name}' from ship {ship_id}",
            "equipment_type": equipment_type,
        }

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

        After this call:
        - ``dst_ship.<kind>`` contains the fitting subset of src's items.
        - ``src_ship.<kind>`` is cleared (the missing-clear bug fix).
        - Inventory is incremented for every overflow item.

        Net effect: each item-name appears in exactly one new place.

        Returns ``{transferred, overflowed, breakdown}``.  When ``src_ship``
        is None (player has no prior ship), returns zero counts.
        """
        breakdown: dict[str, dict[str, list[str]]] = {
            kind: {"transferred": [], "overflowed": []} for kind in _SLOT_KINDS
        }
        if src_ship is None:
            return {"transferred": 0, "overflowed": 0, "breakdown": breakdown}

        for kind in _SLOT_KINDS:
            src_items = self._get_slot(src_ship, kind)
            cap = slot_limits.get(kind, 0) or 0
            fitting = src_items[:cap]
            overflow = src_items[cap:]

            # Push overflow to inventory (concrete type via STI discriminator)
            for name in overflow:
                concrete = await self._resolve_concrete_type(db, name, fallback_kind=kind)
                await self.inventory_repo.add_item(db, player_id, concrete, name, 1, commit=False)

            # Apply to dst, clear src
            self._set_slot(dst_ship, kind, fitting)
            self._set_slot(src_ship, kind, [])

            breakdown[kind]["transferred"] = list(fitting)
            breakdown[kind]["overflowed"] = list(overflow)

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
        items_returned: list[str] = []
        items_returned_detail: dict[str, list[str]] = {kind: [] for kind in _SLOT_KINDS}
        duplicates_dropped = 0

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
                    pass
                # Add the legitimate copy to inventory
                concrete = await self._resolve_concrete_type(db, name, fallback_kind=kind)
                await self.inventory_repo.add_item(db, ship.player_id, concrete, name, 1, commit=False)
                items_returned.append(name)
                items_returned_detail[kind].append(name)
            # Clear this ship's slot
            self._set_slot(ship, kind, [])

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
        ship = await self.player_ship_repo.get_by_id(db, target_ship_id)
        if ship is None:
            raise ValueError(f"Ship {target_ship_id} not found")
        if ship.player_id != player_id:
            raise ValueError(f"Ship {target_ship_id} does not belong to player {player_id}")

        caps = await self._get_static_ship_caps(db, ship)

        evacuated: dict[str, list[str]] = {kind: [] for kind in _SLOT_KINDS}
        any_evacuated = False

        for kind in _SLOT_KINDS:
            current = self._get_slot(ship, kind)
            cap = caps[kind]
            if len(current) > cap:
                keep = current[:cap]
                overflow = current[cap:]
                self._set_slot(ship, kind, keep)
                for name in overflow:
                    concrete = await self._resolve_concrete_type(db, name, fallback_kind=kind)
                    await self.inventory_repo.add_item(db, player_id, concrete, name, 1, commit=False)
                evacuated[kind] = list(overflow)
                any_evacuated = True

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
                if len(cleaned) != len(current) and not dry_run:
                    self._set_slot(ship, kind, cleaned)

        if not dry_run and ships_modified:
            await db.flush()

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
