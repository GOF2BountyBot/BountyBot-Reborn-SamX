"""
Equipment Service for the BountyBot inventory system.

Handles business logic for equipping and unequipping items on ships,
including ownership validation, slot availability checks, and
inventory management.
"""

from typing import Any

from persist.repositories.inventory_repository import InventoryRepository
from persist.repositories.item_repository import ItemRepository
from persist.repositories.player_ship_repository import PlayerShipRepository
from persist.repositories.ship_repository import ShipRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

flogger = bblogger.get_logger("equipment-service")

# Valid equipment types
VALID_EQUIPMENT_TYPES = {"weapons", "modules", "turrets"}

# Map equipment_type → ship slot field
_SLOT_MAP: dict[str, str] = {
    "weapons": "max_primaries",
    "modules": "max_modules",
    "turrets": "max_turrets",
}

# Map equipment_type → inventory item_type
_INVENTORY_TYPE_MAP: dict[str, str] = {
    "weapons": "weapon",
    "modules": "module",
    "turrets": "turret",
}


class EquipmentService:
    """Service for equipping and unequipping items on player ships."""

    def __init__(self) -> None:
        self.ship_repo = PlayerShipRepository()
        self.inventory_repo = InventoryRepository()
        self.item_repo = ItemRepository()
        self.ship_data_repo = ShipRepository()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def equip_item(
        self,
        db: AsyncSession,
        player_id: int,
        ship_id: int,
        equipment_type: str,
        item_name: str,
    ) -> dict[str, Any]:
        """Equip an item from the player's inventory onto a ship.

        Validation chain:
        1. equipment_type is valid
        2. Ship exists and belongs to player
        3. Item exists in game data
        4. Player owns item in inventory
        5. Ship has an available slot for this equipment type
        6. Add item to ship loadout
        7. Remove item from inventory

        Returns a result dict with ``success``, ``ship``, and ``message`` keys.

        Raises:
            ValueError: for any validation failure (caller maps to HTTP 400/404).
        """
        try:
            flogger.debug(
                f"equip_item: player_id={player_id}, ship_id={ship_id}, "
                f"equipment_type={equipment_type}, item_name={item_name}"
            )
            # 1. Validate equipment_type
            flogger.trace(f"Validating equipment_type: {equipment_type}")
            self._validate_equipment_type(equipment_type)
            flogger.trace(f"equipment_type is valid: {equipment_type}")

            # 2. Ship exists and belongs to player
            flogger.trace(f"Validating ship ownership: ship_id={ship_id}, player_id={player_id}")
            ship = await self._get_owned_ship(db, player_id, ship_id)
            flogger.trace(f"Ship ownership validated: ship_id={ship_id}")

            # 3. Item exists in game data
            flogger.trace(f"Mapping equipment_type to inventory_type: {equipment_type}")
            inventory_type = self._map_equipment_type_to_inventory_type(equipment_type)
            flogger.trace(f"Validating item exists in game data: item_name={item_name}, "
                         f"inventory_type={inventory_type}")
            await self._validate_item_exists(db, item_name, inventory_type)
            flogger.trace(f"Item exists in game data: item_name={item_name}")

            # 4. Player owns item in inventory
            flogger.trace(f"Checking player inventory: player_id={player_id}, item_name={item_name}")
            inv_item = await self.inventory_repo.get_player_item(
                db, player_id, inventory_type, item_name
            )
            if not inv_item:
                flogger.warning(
                    f"Item not found in player inventory: player_id={player_id}, "
                    f"item_name={item_name}, inventory_type={inventory_type}"
                )
                raise ValueError(
                    f"Item '{item_name}' (type={inventory_type}) not found in player {player_id} inventory"
                )
            flogger.trace(f"Item found in player inventory: player_id={player_id}, item_name={item_name}")

            # 5. Check slot availability
            flogger.trace(f"Validating slot availability: ship_id={ship_id}, equipment_type={equipment_type}")
            await self._validate_slot_available(db, ship, equipment_type)
            flogger.trace(f"Slot available for {equipment_type} on ship {ship_id}")

            # 6. Add item to ship loadout
            updated_ship = await self.ship_repo.add_equipment(
                db, ship_id, equipment_type, item_name
            )

            # 7. Remove item from inventory
            await self.inventory_repo.remove_item(
                db, player_id, inventory_type, item_name, quantity=1
            )

            flogger.info(
                f"Player {player_id} equipped '{item_name}' ({equipment_type}) on ship {ship_id}"
            )
            return {
                "success": True,
                "ship": updated_ship,
                "message": f"Successfully equipped '{item_name}' on ship {ship_id}",
            }

        except ValueError:
            raise
        except Exception as e:
            flogger.error(f"Unexpected error equipping item: {e}")
            raise

    async def unequip_item(
        self,
        db: AsyncSession,
        player_id: int,
        ship_id: int,
        equipment_type: str,
        item_name: str,
    ) -> dict[str, Any]:
        """Unequip an item from a ship back to the player's inventory.

        Validation chain:
        1. equipment_type is valid
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
                f"equipment_type={equipment_type}, item_name={item_name}"
            )
            # 1. Validate equipment_type
            flogger.trace(f"Validating equipment_type: {equipment_type}")
            self._validate_equipment_type(equipment_type)
            flogger.trace(f"equipment_type is valid: {equipment_type}")

            # 2. Ship exists and belongs to player
            flogger.trace(f"Validating ship ownership: ship_id={ship_id}, player_id={player_id}")
            ship = await self._get_owned_ship(db, player_id, ship_id)
            flogger.trace(f"Ship ownership validated: ship_id={ship_id}")

            # 3. Item is currently equipped on ship
            flogger.trace(f"Checking equipped items: ship_id={ship_id}, equipment_type={equipment_type}")
            equipped_items: list[str] = self._get_equipment_list(ship, equipment_type)
            flogger.trace(f"Equipped items for {equipment_type}: {equipped_items}")
            if item_name not in equipped_items:
                flogger.warning(
                    f"Item not equipped on ship: player_id={player_id}, ship_id={ship_id}, "
                    f"item_name={item_name}, equipment_type={equipment_type}"
                )
                raise ValueError(
                    f"Item '{item_name}' is not equipped in {equipment_type} on ship {ship_id}"
                )

            # 4. Remove item from ship loadout
            updated_ship = await self.ship_repo.remove_equipment(
                db, ship_id, equipment_type, item_name
            )

            # 5. Add item back to inventory
            inventory_type = self._map_equipment_type_to_inventory_type(equipment_type)
            await self.inventory_repo.add_item(
                db, player_id, inventory_type, item_name, quantity=1
            )

            flogger.info(
                f"Player {player_id} unequipped '{item_name}' ({equipment_type}) from ship {ship_id}"
            )
            return {
                "success": True,
                "ship": updated_ship,
                "message": f"Successfully unequipped '{item_name}' from ship {ship_id}",
            }

        except ValueError:
            raise
        except Exception as e:
            flogger.error(f"Unexpected error unequipping item: {e}")
            raise

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
                f"Invalid equipment_type '{equipment_type}'. "
                f"Must be one of: {sorted(VALID_EQUIPMENT_TYPES)}"
            )

    def _get_equipment_list(self, ship: Any, equipment_type: str) -> list[str]:
        """Return the list of equipped items for the given slot type."""
        flogger.trace(f"Retrieving equipment list: ship_id={ship.id}, equipment_type={equipment_type}")
        attr_map = {"weapons": ship.weapons, "modules": ship.modules, "turrets": ship.turrets}
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
            raise ValueError(
                f"Ship {ship_id} does not belong to player {player_id}"
            )
        flogger.trace(f"Ship ownership verified: ship_id={ship_id}, player_id={player_id}")
        return ship

    async def _validate_item_exists(
        self, db: AsyncSession, item_name: str, inventory_type: str
    ) -> None:
        """Verify the item exists in the game data catalogue.

        The ItemRepository stores the item_type as e.g. ``primary_weapon``,
        ``module``, ``turret_weapon``.  We map ``weapon`` → ``primary_weapon``
        and ``turret`` → ``turret_weapon`` before the lookup.

        Raises:
            ValueError: if item not found in game data.
        """
        flogger.trace(f"Validating item exists in game data: item_name={item_name}, "
                     f"inventory_type={inventory_type}")
        game_type_map = {
            "weapon": "primary_weapon",
            "module": "module",
            "turret": "turret_weapon",
        }
        game_item_type = game_type_map.get(inventory_type, inventory_type)
        flogger.trace(f"Mapped inventory_type to game_item_type: {inventory_type} -> {game_item_type}")
        item = await self.item_repo.get_by_name(db, item_name, item_type=game_item_type)
        if not item:
            flogger.warning(
                f"Item not found in game data: item_name={item_name}, game_item_type={game_item_type}"
            )
            raise ValueError(
                f"Item '{item_name}' (type={game_item_type}) not found in game data"
            )
        flogger.trace(f"Item found in game data: item_name={item_name}, game_item_type={game_item_type}")

    async def _validate_slot_available(
        self, db: AsyncSession, ship: Any, equipment_type: str
    ) -> None:
        """Check that the ship has a free slot for the given equipment type.

        Raises:
            ValueError: if the ship's slots for this type are full.
        """
        flogger.trace(f"Validating slot availability: ship_name={ship.ship_name}, "
                     f"equipment_type={equipment_type}")
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
