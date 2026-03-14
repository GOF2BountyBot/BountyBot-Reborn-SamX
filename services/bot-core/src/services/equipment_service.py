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
            # 1. Validate equipment_type
            self._validate_equipment_type(equipment_type)

            # 2. Ship exists and belongs to player
            ship = await self._get_owned_ship(db, player_id, ship_id)

            # 3. Item exists in game data
            inventory_type = self._map_equipment_type_to_inventory_type(equipment_type)
            await self._validate_item_exists(db, item_name, inventory_type)

            # 4. Player owns item in inventory
            inv_item = await self.inventory_repo.get_player_item(
                db, player_id, inventory_type, item_name
            )
            if not inv_item:
                raise ValueError(
                    f"Item '{item_name}' (type={inventory_type}) not found in player {player_id} inventory"
                )

            # 5. Check slot availability
            await self._validate_slot_available(db, ship, equipment_type)

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
            # 1. Validate equipment_type
            self._validate_equipment_type(equipment_type)

            # 2. Ship exists and belongs to player
            ship = await self._get_owned_ship(db, player_id, ship_id)

            # 3. Item is currently equipped on ship
            equipped_items: list[str] = self._get_equipment_list(ship, equipment_type)
            if item_name not in equipped_items:
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
        slot_field = _SLOT_MAP.get(equipment_type)
        if slot_field is None:
            raise ValueError(f"Invalid equipment_type: '{equipment_type}'")
        return slot_field

    def _map_equipment_type_to_inventory_type(self, equipment_type: str) -> str:
        """Map equipment_type to the inventory item_type string."""
        inv_type = _INVENTORY_TYPE_MAP.get(equipment_type)
        if inv_type is None:
            raise ValueError(f"Invalid equipment_type: '{equipment_type}'")
        return inv_type

    def _validate_equipment_type(self, equipment_type: str) -> None:
        """Raise ValueError if equipment_type is not recognised."""
        if equipment_type not in VALID_EQUIPMENT_TYPES:
            raise ValueError(
                f"Invalid equipment_type '{equipment_type}'. "
                f"Must be one of: {sorted(VALID_EQUIPMENT_TYPES)}"
            )

    def _get_equipment_list(self, ship: Any, equipment_type: str) -> list[str]:
        """Return the list of equipped items for the given slot type."""
        attr_map = {"weapons": ship.weapons, "modules": ship.modules, "turrets": ship.turrets}
        raw = attr_map.get(equipment_type)
        return list(raw) if raw else []

    async def _get_owned_ship(self, db: AsyncSession, player_id: int, ship_id: int) -> Any:
        """Retrieve a ship and verify ownership.

        Raises:
            ValueError: if ship not found or does not belong to player.
        """
        ship = await self.ship_repo.get_by_id(db, ship_id)
        if not ship:
            raise ValueError(f"Ship {ship_id} not found")
        if ship.player_id != player_id:
            raise ValueError(
                f"Ship {ship_id} does not belong to player {player_id}"
            )
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
        game_type_map = {
            "weapon": "primary_weapon",
            "module": "module",
            "turret": "turret_weapon",
        }
        game_item_type = game_type_map.get(inventory_type, inventory_type)
        item = await self.item_repo.get_by_name(db, item_name, item_type=game_item_type)
        if not item:
            raise ValueError(
                f"Item '{item_name}' (type={game_item_type}) not found in game data"
            )

    async def _validate_slot_available(
        self, db: AsyncSession, ship: Any, equipment_type: str
    ) -> None:
        """Check that the ship has a free slot for the given equipment type.

        Raises:
            ValueError: if the ship's slots for this type are full.
        """
        slot_field = self._map_equipment_type_to_slot(equipment_type)

        # Get static ship data for the slot limit
        ship_data = await self.ship_data_repo.get_by_name(db, ship.ship_name)
        if not ship_data:
            raise ValueError(f"Static ship data not found for '{ship.ship_name}'")

        max_slots: int = getattr(ship_data, slot_field)
        current_count: int = ship.get_equipped_count(equipment_type)

        if current_count >= max_slots:
            raise ValueError(
                f"No available {equipment_type} slots on ship '{ship.ship_name}' "
                f"({current_count}/{max_slots} slots used)"
            )
