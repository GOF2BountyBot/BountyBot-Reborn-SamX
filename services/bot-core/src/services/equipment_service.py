"""
Equipment Service for the BountyBot inventory system.

Handles business logic for equipping and unequipping items on ships,
including ownership validation, slot availability checks, and
inventory management.
"""

from typing import Any

from persist.repositories.inventory_repository import InventoryRepository
from persist.repositories.item_repository import ItemRepository
from persist.repositories.module_repository import ModuleRepository
from persist.repositories.player_ship_repository import PlayerShipRepository
from persist.repositories.ship_repository import ShipRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

from services.exceptions import InvalidItemTypeError
from services.game_constants import GameConstants

flogger = bblogger.get_logger("equipment-service")

# Valid equipment types (includes secondary_weapons for data-model completeness;
# UX surface gates secondary_weapon via GameConstants.CURRENTLY_ENABLED_TYPES)
VALID_EQUIPMENT_TYPES = {"weapons", "secondary_weapons", "modules", "turrets"}

# Map equipment_type → ship slot field name
_SLOT_MAP: dict[str, str] = {
    "weapons": "max_primaries",
    "secondary_weapons": "max_secondaries",
    "modules": "max_modules",
    "turrets": "max_turrets",
}

# Map equipment_type → concrete inventory item_type (A.36 fix: concrete, not generic)
_INVENTORY_TYPE_MAP: dict[str, str] = {
    "weapons": "primary_weapon",       # was "weapon" — now concrete
    "secondary_weapons": "secondary_weapon",
    "modules": "module",
    "turrets": "turret_weapon",        # was "turret" — now concrete
}

# Map Item.type STI discriminator → equipment_category
_ITEM_TYPE_TO_EQUIPMENT_CATEGORY: dict[str, str] = {
    "PrimaryWeapon": "weapons",
    "SecondaryWeapon": "secondary_weapons",  # was "weapons" — now routes to correct slot
    "TurretWeapon": "turrets",
}

# Map Item.type STI discriminator → concrete inventory item_type string.
# Used by write-site fixers (admin.py, ships.py, shop_service.py) to avoid
# persisting generic aliases.
_ITEM_TYPE_TO_CONCRETE_INVENTORY_TYPE: dict[str, str] = {
    "PrimaryWeapon": "primary_weapon",
    "SecondaryWeapon": "secondary_weapon",
    "TurretWeapon": "turret_weapon",
}


def item_discriminator_to_concrete_type(discriminator: str) -> str | None:
    """Map an ``Item.type`` STI discriminator string to a concrete inventory item_type.

    Returns one of ``"primary_weapon"``, ``"secondary_weapon"``,
    ``"turret_weapon"``, ``"module"``, ``"ship"``, or ``None`` if the
    discriminator is unrecognised.

    Used by write-site helpers (admin_remove_ship, transfer_ship, sell_ship,
    purchase_ship overflow) to resolve the concrete item_type from the STI
    table without storing generic aliases.
    """
    if discriminator in _ITEM_TYPE_TO_CONCRETE_INVENTORY_TYPE:
        return _ITEM_TYPE_TO_CONCRETE_INVENTORY_TYPE[discriminator]
    if discriminator.endswith("Module"):
        return "module"
    if discriminator == "Ship":
        return "ship"
    return None


def _item_type_to_equipment_category(item_type: str) -> str | None:
    """Convert an ``Item.type`` string to an equipment category.

    Returns ``"weapons"``, ``"modules"``, ``"turrets"``, or ``None``.
    """
    if item_type in _ITEM_TYPE_TO_EQUIPMENT_CATEGORY:
        return _ITEM_TYPE_TO_EQUIPMENT_CATEGORY[item_type]
    if item_type.endswith("Module"):
        return "modules"
    return None


def _item_type_to_inventory_type(item_type: str) -> str | None:
    """Convert an ``Item.type`` string to a concrete inventory item_type string.

    Returns one of ``"primary_weapon"``, ``"secondary_weapon"``,
    ``"turret_weapon"``, ``"module"``, or ``None``.
    """
    category = _item_type_to_equipment_category(item_type)
    if category is None:
        return None
    return _INVENTORY_TYPE_MAP[category]


class EquipmentService:
    """Service for equipping and unequipping items on player ships."""

    def __init__(self) -> None:
        self.ship_repo = PlayerShipRepository()
        self.inventory_repo = InventoryRepository()
        self.item_repo = ItemRepository()
        self.module_repo = ModuleRepository()
        self.ship_data_repo = ShipRepository()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def equip_item(
        self,
        db: AsyncSession,
        player_id: int,
        ship_id: int,
        item_name: str,
        equipment_type: str | None = None,
    ) -> dict[str, Any]:
        """Equip an item from the player's inventory onto a ship.

        If *equipment_type* is ``None`` the type is auto-detected from the item
        name via :meth:`_resolve_equipment_type`.

        Validation chain:
        1. Resolve / validate equipment_type
        2. Ship exists and belongs to player
        3. Item exists in game data
        4. Player owns item in inventory
        5. Ship has an available slot for this equipment type
        6. MODULE_EQUIP_LIMITS enforcement (modules only)
        7. Add item to ship loadout
        8. Remove item from inventory

        Returns a result dict with ``success``, ``ship``, and ``message`` keys.

        Raises:
            ValueError: for any validation failure (caller maps to HTTP 400/404).
        """
        try:
            flogger.debug(
                f"equip_item: player_id={player_id}, ship_id={ship_id}, "
                f"equipment_type={equipment_type!r}, item_name={item_name}"
            )
            # 1. Resolve equipment_type (auto-detect if not provided)
            if equipment_type is None:
                equipment_type = await self._resolve_equipment_type(db, item_name)
            else:
                self._validate_equipment_type(equipment_type)
            flogger.trace(f"Resolved equipment_type: {equipment_type}")

            # Defense-in-depth: reject secondary_weapon equip when gated off.
            # The cog-side filter also prevents this, but we enforce it here too.
            _sec_gated = equipment_type == "secondary_weapons" and (
                "secondary_weapon" not in GameConstants.CURRENTLY_ENABLED_TYPES
            )
            if _sec_gated:
                raise InvalidItemTypeError("Secondary weapons are not currently enabled")

            # 2. Ship exists and belongs to player
            ship = await self._get_owned_ship(db, player_id, ship_id)

            # 3. Item exists in game data
            inventory_type = self._map_equipment_type_to_inventory_type(equipment_type)
            await self._validate_item_exists(db, item_name, inventory_type)

            # 4. Player owns item in inventory
            inv_item = await self.inventory_repo.get_player_item(db, player_id, inventory_type, item_name)
            if not inv_item:
                flogger.warning(
                    f"Item not found in player inventory: player_id={player_id}, "
                    f"item_name={item_name}, inventory_type={inventory_type}"
                )
                raise ValueError(
                    f"Item '{item_name}' (type={inventory_type}) not found in player {player_id} inventory"
                )

            # 5. Check slot availability
            await self._validate_slot_available(db, ship, equipment_type)

            # 6. MODULE_EQUIP_LIMITS enforcement
            if equipment_type == "modules":
                await self._validate_module_equip_limit(db, ship, item_name)

            # 7. Add item to ship loadout
            updated_ship = await self.ship_repo.add_equipment(db, ship_id, equipment_type, item_name)

            # 8. Remove item from inventory
            await self.inventory_repo.remove_item(db, player_id, inventory_type, item_name, quantity=1)

            flogger.info(f"Player {player_id} equipped '{item_name}' ({equipment_type}) on ship {ship_id}")
            return {
                "success": True,
                "ship": updated_ship,
                "message": f"Successfully equipped '{item_name}' on ship {ship_id}",
            }

        except (ValueError, InvalidItemTypeError):
            raise
        except Exception as e:
            flogger.error(f"Unexpected error equipping item: {e}")
            raise

    async def unequip_item(
        self,
        db: AsyncSession,
        player_id: int,
        ship_id: int,
        item_name: str,
        equipment_type: str | None = None,
    ) -> dict[str, Any]:
        """Unequip an item from a ship back to the player's inventory.

        If *equipment_type* is ``None`` it is auto-detected from the item name.
        If auto-detection fails, the item is searched for across all equipped
        slots on the ship.

        Validation chain:
        1. Resolve / validate equipment_type
        2. Ship exists and belongs to player
        3. Item is currently equipped on ship
        4. Remove item from ship loadout
        5. Add item to inventory

        Returns a result dict with ``success``, ``ship``, and ``message`` keys.

        Raises:
            ValueError: for any validation failure (caller maps to HTTP 400/404).
        """
        try:
            flogger.debug(
                f"unequip_item: player_id={player_id}, ship_id={ship_id}, "
                f"equipment_type={equipment_type!r}, item_name={item_name}"
            )
            # 2. Ship exists and belongs to player (do this first so we have ship for fallback)
            ship = await self._get_owned_ship(db, player_id, ship_id)

            # 1. Resolve equipment_type (auto-detect if not provided)
            if equipment_type is None:
                # Try item lookup first
                try:
                    equipment_type = await self._resolve_equipment_type(db, item_name)
                except ValueError:
                    # Fallback: scan all equipped slots for the item
                    equipment_type = self._find_item_in_equipped_slots(ship, item_name)
                    if equipment_type is None:
                        raise ValueError(
                            f"Item '{item_name}' not found in any equipped slot on ship {ship_id}"
                        ) from None
            else:
                self._validate_equipment_type(equipment_type)
            flogger.trace(f"Resolved equipment_type: {equipment_type}")

            # 3. Item is currently equipped on ship
            equipped_items: list[str] = self._get_equipment_list(ship, equipment_type)
            if item_name not in equipped_items:
                flogger.warning(
                    f"Item not equipped on ship: player_id={player_id}, ship_id={ship_id}, "
                    f"item_name={item_name}, equipment_type={equipment_type}"
                )
                raise ValueError(f"Item '{item_name}' is not equipped in {equipment_type} on ship {ship_id}")

            # 4. Remove item from ship loadout
            updated_ship = await self.ship_repo.remove_equipment(db, ship_id, equipment_type, item_name)

            # 5. Add item back to inventory
            inventory_type = self._map_equipment_type_to_inventory_type(equipment_type)
            await self.inventory_repo.add_item(db, player_id, inventory_type, item_name, quantity=1)

            flogger.info(f"Player {player_id} unequipped '{item_name}' ({equipment_type}) from ship {ship_id}")
            return {
                "success": True,
                "ship": updated_ship,
                "message": f"Successfully unequipped '{item_name}' from ship {ship_id}",
            }

        except (ValueError, InvalidItemTypeError):
            raise
        except Exception as e:
            flogger.error(f"Unexpected error unequipping item: {e}")
            raise

    async def equip_check(
        self,
        db: AsyncSession,
        player_id: int,
        ship_id: int,
        item_name: str,
    ) -> dict[str, Any]:
        """Pre-flight check before equipping — does not modify any data.

        Returns a dict with a ``status`` key:

        - ``"ok"``             — item can be equipped (also includes ``equipment_type``, ``item_type``)
        - ``"slot_full"``      — all slots occupied (also includes ``equipped_items``, ``max_slots``)
        - ``"unique_conflict"``— unique module class already equipped (includes ``conflicting_item``,
                                 ``module_class``, ``max_equipped``)

        Raises:
            ValueError: if the item doesn't exist in game data or is not equippable.
        """
        flogger.debug(f"equip_check: player_id={player_id}, ship_id={ship_id}, item_name={item_name!r}")

        # 1. Look up item across all types to get its type string
        base_item = await self.item_repo.get_by_name_any_type(db, item_name)
        if not base_item:
            raise ValueError(f"Item '{item_name}' not found in game data")

        item_type_str = base_item.type  # e.g. "ArmourModule", "PrimaryWeapon"
        equipment_category = _item_type_to_equipment_category(item_type_str)
        if equipment_category is None:
            raise ValueError(f"Item '{item_name}' (type={item_type_str!r}) is not equippable")

        # Defense-in-depth: reject secondary_weapon equip when gated off.
        _sec_gated = (
            equipment_category == "secondary_weapons"
            and "secondary_weapon" not in GameConstants.CURRENTLY_ENABLED_TYPES
        )
        if _sec_gated:
            raise InvalidItemTypeError("Secondary weapons are not currently enabled")

        # 2. Ship exists and belongs to player
        ship = await self._get_owned_ship(db, player_id, ship_id)

        # 3. Check if player owns item in inventory
        inventory_type = _INVENTORY_TYPE_MAP[equipment_category]
        inv_item = await self.inventory_repo.get_player_item(db, player_id, inventory_type, item_name)
        if not inv_item:
            raise ValueError(f"Item '{item_name}' (type={inventory_type}) not found in player {player_id} inventory")

        # 4. Check slot availability
        slot_field = _SLOT_MAP[equipment_category]
        ship_data = await self.ship_data_repo.get_by_name(db, ship.ship_name)
        if not ship_data:
            raise ValueError(f"Static ship data not found for '{ship.ship_name}'")

        max_slots: int = getattr(ship_data, slot_field)
        current_equipped: list[str] = self._get_equipment_list(ship, equipment_category)
        current_count: int = len(current_equipped)

        if current_count >= max_slots:
            # Slots are full — return slot_full with equipped items for swap UI
            equipped_items_info = [{"name": name, "emoji": ""} for name in current_equipped]
            flogger.debug(
                f"equip_check: slot_full for {equipment_category} on ship {ship_id} ({current_count}/{max_slots})"
            )
            return {
                "status": "slot_full",
                "equipment_type": equipment_category,
                "item_type": item_type_str,
                "max_slots": max_slots,
                "equipped_items": equipped_items_info,
            }

        # 5. Module-specific: check MODULE_EQUIP_LIMITS
        if equipment_category == "modules":
            module_class = item_type_str  # e.g. "ArmourModule"
            limit = GameConstants.MODULE_EQUIP_LIMITS.get(module_class)
            if limit is not None and limit >= 0:
                # Count how many of this module class are already equipped
                conflicting = await self._find_conflicting_module(db, ship, module_class)
                if conflicting is not None and limit <= 1:
                    flogger.debug(
                        f"equip_check: unique_conflict for module_class={module_class} "
                        f"on ship {ship_id}, conflicting={conflicting!r}"
                    )
                    return {
                        "status": "unique_conflict",
                        "equipment_type": equipment_category,
                        "item_type": item_type_str,
                        "module_class": module_class,
                        "max_equipped": limit,
                        "conflicting_item": {"name": conflicting, "emoji": ""},
                    }

        # 6. All checks passed
        flogger.debug(f"equip_check: ok for item={item_name!r} on ship {ship_id}")
        return {
            "status": "ok",
            "equipment_type": equipment_category,
            "item_type": item_type_str,
        }

    # ------------------------------------------------------------------
    # Helpers / mapping
    # ------------------------------------------------------------------

    def _map_equipment_type_to_slot(self, equipment_type: str) -> str:
        """Map equipment_type to the Ship model's max-slot field name."""
        flogger.trace(f"Mapping equipment_type to slot field: {equipment_type}")
        slot_field = _SLOT_MAP.get(equipment_type)
        if slot_field is None:
            flogger.warning(f"No slot mapping found for equipment_type: {equipment_type}")
            raise ValueError(f"Invalid equipment_type: '{equipment_type}'")
        flogger.trace(f"Slot field mapped: {equipment_type} -> {slot_field}")
        return slot_field

    def _map_equipment_type_to_inventory_type(self, equipment_type: str) -> str:
        """Map equipment_type to the inventory item_type string."""
        flogger.trace(f"Mapping equipment_type to inventory_type: {equipment_type}")
        inv_type = _INVENTORY_TYPE_MAP.get(equipment_type)
        if inv_type is None:
            flogger.warning(f"No inventory mapping found for equipment_type: {equipment_type}")
            raise ValueError(f"Invalid equipment_type: '{equipment_type}'")
        flogger.trace(f"Inventory type mapped: {equipment_type} -> {inv_type}")
        return inv_type

    def _validate_equipment_type(self, equipment_type: str) -> None:
        """Raise ValueError if equipment_type is not recognised."""
        if equipment_type not in VALID_EQUIPMENT_TYPES:
            flogger.warning(
                f"Invalid equipment_type: {equipment_type}. Must be one of: {sorted(VALID_EQUIPMENT_TYPES)}"
            )
            raise ValueError(
                f"Invalid equipment_type '{equipment_type}'. Must be one of: {sorted(VALID_EQUIPMENT_TYPES)}"
            )

    def _get_equipment_list(self, ship: Any, equipment_type: str) -> list[str]:
        """Return the list of equipped items for the given slot type."""
        flogger.trace(f"Retrieving equipment list: ship_id={ship.id}, equipment_type={equipment_type}")
        attr_map = {
            "weapons": ship.weapons,
            "secondary_weapons": getattr(ship, "secondary_weapons", None),
            "modules": ship.modules,
            "turrets": ship.turrets,
        }
        raw = attr_map.get(equipment_type)
        result = list(raw) if raw else []
        flogger.trace(f"Equipment list for {equipment_type}: {result}")
        return result

    async def _get_owned_ship(self, db: AsyncSession, player_id: int, ship_id: int) -> Any:
        """Retrieve a ship and verify ownership.

        Raises:
            ValueError: if ship not found or does not belong to player.
        """
        flogger.trace(f"Retrieving ship: ship_id={ship_id}")
        ship = await self.ship_repo.get_by_id(db, ship_id)
        if not ship:
            flogger.warning(f"Ship not found: ship_id={ship_id}")
            raise ValueError(f"Ship {ship_id} not found")
        flogger.trace(f"Ship retrieved: ship_id={ship_id}, ship_name={ship.ship_name}")
        if ship.player_id != player_id:
            flogger.warning(
                f"Ship ownership mismatch: ship_id={ship_id}, "
                f"expected player_id={player_id}, actual player_id={ship.player_id}"
            )
            raise ValueError(f"Ship {ship_id} does not belong to player {player_id}")
        flogger.trace(f"Ship ownership verified: ship_id={ship_id}, player_id={player_id}")
        return ship

    async def _validate_item_exists(self, db: AsyncSession, item_name: str, inventory_type: str) -> None:
        """Verify the item exists in the game data catalogue.

        The ItemRepository stores the item_type as e.g. ``primary_weapon``,
        ``module``, ``turret_weapon``.  We map ``weapon`` → ``primary_weapon``
        and ``turret`` → ``turret_weapon`` before the lookup.

        Raises:
            ValueError: if item not found in game data.
        """
        flogger.trace(f"Validating item exists in game data: item_name={item_name}, inventory_type={inventory_type}")
        # _INVENTORY_TYPE_MAP now returns concrete types; pass through directly
        game_item_type = inventory_type
        flogger.trace(f"Mapped inventory_type to game_item_type: {inventory_type} -> {game_item_type}")
        item = await self.item_repo.get_by_name(db, item_name, item_type=game_item_type)
        if not item:
            flogger.warning(f"Item not found in game data: item_name={item_name}, game_item_type={game_item_type}")
            raise ValueError(f"Item '{item_name}' (type={game_item_type}) not found in game data")
        flogger.trace(f"Item found in game data: item_name={item_name}, game_item_type={game_item_type}")

    async def _validate_slot_available(self, db: AsyncSession, ship: Any, equipment_type: str) -> None:
        """Check that the ship has a free slot for the given equipment type.

        Raises:
            ValueError: if the ship's slots for this type are full.
        """
        flogger.trace(f"Validating slot availability: ship_name={ship.ship_name}, equipment_type={equipment_type}")
        slot_field = self._map_equipment_type_to_slot(equipment_type)

        # Get static ship data for the slot limit
        flogger.trace(f"Retrieving static ship data: ship_name={ship.ship_name}")
        ship_data = await self.ship_data_repo.get_by_name(db, ship.ship_name)
        if not ship_data:
            flogger.error(f"Static ship data not found: ship_name={ship.ship_name}")
            raise ValueError(f"Static ship data not found for '{ship.ship_name}'")

        max_slots: int = getattr(ship_data, slot_field)
        current_count: int = ship.get_equipped_count(equipment_type)
        flogger.trace(f"Slot status: equipment_type={equipment_type}, current={current_count}, max={max_slots}")

        if current_count >= max_slots:
            flogger.warning(
                f"No available slots for equipment: ship_name={ship.ship_name}, "
                f"equipment_type={equipment_type}, current={current_count}, max={max_slots}"
            )
            raise ValueError(
                f"No available {equipment_type} slots on ship '{ship.ship_name}' "
                f"({current_count}/{max_slots} slots used)"
            )
        flogger.trace(f"Slot available: equipment_type={equipment_type}, slot {current_count + 1}/{max_slots}")

    async def _validate_module_equip_limit(self, db: AsyncSession, ship: Any, item_name: str) -> None:
        """Enforce MODULE_EQUIP_LIMITS for the given module item.

        Raises:
            ValueError: if the unique module class limit is already reached.
        """
        # Look up the module to get its class (Item.type)
        base_item = await self.item_repo.get_by_name_any_type(db, item_name)
        if not base_item:
            flogger.warning(f"Module not found in item table for limit check: item_name={item_name}")
            return  # Already validated as existing; skip gracefully

        module_class = base_item.type  # e.g. "ArmourModule"
        limit = GameConstants.MODULE_EQUIP_LIMITS.get(module_class)
        if limit is None:
            # Unknown module class — no limit enforced
            flogger.trace(f"No MODULE_EQUIP_LIMITS entry for module_class={module_class!r}; skipping")
            return

        if limit == 0:
            raise ValueError(f"Module class '{module_class}' cannot be equipped (limit=0)")

        if limit < 0:
            # -1 means unlimited
            flogger.trace(f"Module class {module_class!r} has unlimited equip limit; skipping")
            return

        # Count equipped modules of same class
        conflicting = await self._find_conflicting_module(db, ship, module_class)
        if conflicting is not None:
            flogger.warning(
                f"Module equip limit reached: module_class={module_class}, limit={limit}, conflicting={conflicting!r}"
            )
            raise ValueError(
                f"Cannot equip '{item_name}': module class '{module_class}' is limited to "
                f"{limit} equipped at once. Already equipped: '{conflicting}'"
            )

    async def _find_conflicting_module(self, db: AsyncSession, ship: Any, module_class: str) -> str | None:
        """Return the name of an already-equipped module that has the same class, or None.

        Queries the item table for each equipped module name to find its class.
        Returns the first conflicting module name or ``None`` if no conflict exists.
        """
        equipped_modules: list[str] = self._get_equipment_list(ship, "modules")
        for equipped_name in equipped_modules:
            equipped_item = await self.item_repo.get_by_name_any_type(db, equipped_name)
            if equipped_item and equipped_item.type == module_class:
                flogger.trace(
                    f"Found conflicting module: equipped_name={equipped_name!r}, module_class={module_class!r}"
                )
                return equipped_name
        return None

    async def _resolve_equipment_type(self, db: AsyncSession, item_name: str) -> str:
        """Auto-detect the equipment category from an item name.

        Raises:
            ValueError: if the item is not found or not equippable.
        """
        base_item = await self.item_repo.get_by_name_any_type(db, item_name)
        if not base_item:
            raise ValueError(f"Item '{item_name}' not found in game data")
        category = _item_type_to_equipment_category(base_item.type)
        if category is None:
            raise ValueError(f"Item '{item_name}' (type={base_item.type!r}) is not equippable")
        flogger.trace(f"Auto-detected equipment_type={category!r} for item={item_name!r}")
        return category

    def _find_item_in_equipped_slots(self, ship: Any, item_name: str) -> str | None:
        """Search all equipped slots on a ship for the given item name.

        Returns the equipment category (``"weapons"``, ``"secondary_weapons"``,
        ``"modules"``, ``"turrets"``) containing the item, or ``None`` if not
        found in any slot.
        """
        for category in ("weapons", "secondary_weapons", "modules", "turrets"):
            if item_name in self._get_equipment_list(ship, category):
                return category
        return None
