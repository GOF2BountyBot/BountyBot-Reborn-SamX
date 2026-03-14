"""
Inventory Service for the BountyBot inventory system.

Handles business logic for inventory management including
item storage, quantity tracking, and inventory operations.
"""

from typing import Any

from persist.repositories.inventory_repository import InventoryRepository
from persist.repositories.module_repository import ModuleRepository
from persist.repositories.player_repository import PlayerRepository
from persist.repositories.primary_weapon_repository import PrimaryWeaponRepository
from persist.repositories.secondary_weapon_repository import SecondaryWeaponRepository
from persist.repositories.ship_repository import ShipRepository
from persist.repositories.turret_weapon_repository import TurretWeaponRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

flogger = bblogger.get_logger("inventory-service")

class InventoryService:
    def __init__(self):
        self.inventory_repo = InventoryRepository()
        self.player_repo = PlayerRepository()
        self.ship_repo = ShipRepository()
        self.primary_weapon_repo = PrimaryWeaponRepository()
        self.secondary_weapon_repo = SecondaryWeaponRepository()
        self.turret_weapon_repo = TurretWeaponRepository()
        self.module_repo = ModuleRepository()

    # Valid item types
    VALID_ITEM_TYPES = ["ship", "weapon", "module", "turret"]

    async def get_player_inventory(
        self,
        db: AsyncSession,
        player_id: int,
        item_type: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Get a player's inventory, optionally filtered by item type.
        Returns formatted inventory data with item details.
        """
        try:
            # Verify player exists
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            if item_type and item_type not in self.VALID_ITEM_TYPES:
                raise ValueError(f"Invalid item type: {item_type}")

            # Get inventory items
            items = await self.inventory_repo.get_player_items(db, player_id, item_type)

            # Format items for response
            formatted_items = []
            for item in items:
                formatted_item = {
                    "id": item.id,
                    "item_type": item.item_type,
                    "item_name": item.item_name,
                    "quantity": item.quantity,
                    "acquired_at": item.acquired_at.isoformat(),
                    "item_details": await self._get_item_details(db, item.item_name)
                }
                formatted_items.append(formatted_item)

            flogger.debug(f"Retrieved {len(formatted_items)} inventory items for player {player_id}")
            return formatted_items

        except Exception as e:
            flogger.error(f"Error getting inventory for player {player_id}: {e}")
            raise

    async def add_item_to_inventory(
        self,
        db: AsyncSession,
        player_id: int,
        item_type: str,
        item_name: str,
        quantity: int = 1
    ) -> dict[str, Any]:
        """
        Add items to a player's inventory.
        Returns transaction details.
        """
        try:
            # Validate inputs
            if item_type not in self.VALID_ITEM_TYPES:
                raise ValueError(f"Invalid item type: {item_type}")

            if quantity <= 0:
                raise ValueError("Quantity must be positive")

            # Verify player exists
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            # Validate item exists in static data
            if not await self._validate_item_exists(db, item_name, item_type):
                raise ValueError(f"Item {item_name} does not exist or is not of type {item_type}")

            # Add item to inventory
            inventory_item = await self.inventory_repo.add_item(
                db, player_id, item_type, item_name, quantity
            )

            transaction_details = {
                "player_id": player_id,
                "item_type": item_type,
                "item_name": item_name,
                "quantity_added": quantity,
                "new_total_quantity": inventory_item.quantity,
                "transaction_time": inventory_item.acquired_at.isoformat()
            }

            flogger.info(f"Added {quantity}x {item_name} to player {player_id} inventory")
            return transaction_details

        except Exception as e:
            flogger.error(f"Error adding item to inventory: {e}")
            raise

    async def remove_item_from_inventory(
        self,
        db: AsyncSession,
        player_id: int,
        item_type: str,
        item_name: str,
        quantity: int = 1
    ) -> dict[str, Any]:
        """
        Remove items from a player's inventory.
        Returns transaction details.
        """
        try:
            # Validate inputs
            if item_type not in self.VALID_ITEM_TYPES:
                raise ValueError(f"Invalid item type: {item_type}")

            if quantity <= 0:
                raise ValueError("Quantity must be positive")

            # Verify player exists
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            # Check if player has the item
            existing_item = await self.inventory_repo.get_player_item(
                db, player_id, item_type, item_name
            )

            if not existing_item:
                raise ValueError(f"Player does not have {item_name} in inventory")

            if existing_item.quantity < quantity:
                raise ValueError(
                    f"Insufficient quantity. Available: {existing_item.quantity}, Requested: {quantity}"
                )

            old_quantity = existing_item.quantity

            # Remove item from inventory
            await self.inventory_repo.remove_item(db, player_id, item_type, item_name, quantity)

            # Get updated item (or None if completely removed)
            updated_item = await self.inventory_repo.get_player_item(
                db, player_id, item_type, item_name
            )

            transaction_details = {
                "player_id": player_id,
                "item_type": item_type,
                "item_name": item_name,
                "quantity_removed": quantity,
                "old_quantity": old_quantity,
                "new_quantity": updated_item.quantity if updated_item else 0,
                "item_completely_removed": updated_item is None
            }

            flogger.info(f"Removed {quantity}x {item_name} from player {player_id} inventory")
            return transaction_details

        except Exception as e:
            flogger.error(f"Error removing item from inventory: {e}")
            raise

    async def transfer_item_between_players(
        self,
        db: AsyncSession,
        from_player_id: int,
        to_player_id: int,
        item_type: str,
        item_name: str,
        quantity: int = 1
    ) -> dict[str, Any]:
        """
        Transfer items between players (future feature for trading).
        Returns transfer details.
        """
        try:
            # Validate both players exist and are in same guild
            from_player = await self.player_repo.get_by_id(db, from_player_id)
            to_player = await self.player_repo.get_by_id(db, to_player_id)

            if not from_player or not to_player:
                raise ValueError("One or both players not found")

            if from_player.guild_id != to_player.guild_id:
                raise ValueError("Players must be in the same guild to trade")

            # Perform atomic transfer
            async with db.begin():
                # Remove from source player
                remove_result = await self.remove_item_from_inventory(
                    db, from_player_id, item_type, item_name, quantity
                )

                # Add to target player
                add_result = await self.add_item_to_inventory(
                    db, to_player_id, item_type, item_name, quantity
                )

            transfer_details = {
                "from_player_id": from_player_id,
                "to_player_id": to_player_id,
                "item_type": item_type,
                "item_name": item_name,
                "quantity": quantity,
                "from_player_result": remove_result,
                "to_player_result": add_result
            }

            flogger.info(f"Transferred {quantity}x {item_name} from player {from_player_id} to {to_player_id}")
            return transfer_details

        except Exception as e:
            flogger.error(f"Error transferring item between players: {e}")
            raise

    async def get_inventory_summary(self, db: AsyncSession, player_id: int) -> dict[str, Any]:
        """Get a summary of a player's inventory by item type."""
        try:
            # Verify player exists
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            summary = await self.inventory_repo.get_inventory_summary(db, player_id)

            # Add player context
            summary["player_id"] = player_id
            summary["player_tier"] = player.tier
            summary["guild_id"] = player.guild_id

            return summary

        except Exception as e:
            flogger.error(f"Error getting inventory summary for player {player_id}: {e}")
            raise

    async def search_inventory(
        self,
        db: AsyncSession,
        player_id: int,
        search_term: str
    ) -> list[dict[str, Any]]:
        """Search player's inventory for items matching a search term."""
        try:
            # Get all inventory items
            all_items = await self.get_player_inventory(db, player_id)

            # Filter by search term (case-insensitive)
            search_term_lower = search_term.lower()
            matching_items = [
                item for item in all_items
                if search_term_lower in item["item_name"].lower()
            ]

            flogger.debug(f"Found {len(matching_items)} items matching '{search_term}' for player {player_id}")
            return matching_items

        except Exception as e:
            flogger.error(f"Error searching inventory for player {player_id}: {e}")
            raise

    async def validate_item_compatibility(
        self,
        db: AsyncSession,
        player_id: int,
        ship_name: str,
        item_type: str,
        item_name: str,
        player_ship: Any | None = None,
    ) -> dict[str, Any]:
        """
        Validate if an item can be equipped on a specific ship.
        Returns compatibility information including slot availability.

        If *player_ship* (a PlayerShip ORM object) is supplied, the current
        equipped count is read from ``player_ship.get_equipped_count()``
        (which counts items actually equipped on the ship).  When it is not
        supplied the method falls back to the legacy behaviour of querying
        the global inventory count — kept for backward compatibility but
        deprecated for slot checking.
        """
        try:
            compatibility = {
                "compatible": True,
                "ship_name": ship_name,
                "item_type": item_type,
                "item_name": item_name,
                "reason": None
            }

            # Look up ship slot limits
            ship_details = await self._get_ship_details(db, ship_name)
            if not ship_details:
                compatibility["compatible"] = False
                compatibility["reason"] = f"Ship {ship_name} not found in database"
                return compatibility

            # Map item_type to the ship's slot limit and the equipment_type key
            # used by PlayerShip.get_equipped_count()
            item_type_lower = item_type.lower()
            if item_type_lower in ("weapon", "primary_weapon"):
                max_slots = ship_details["max_primaries"]
                equipment_type_key = "weapons"
            elif item_type_lower == "secondary_weapon":
                max_slots = ship_details["max_secondaries"]
                equipment_type_key = "weapons"  # secondary weapons share the weapons slot key
            elif item_type_lower in ("turret", "turret_weapon"):
                max_slots = ship_details["max_turrets"]
                equipment_type_key = "turrets"
            elif item_type_lower == "module":
                max_slots = ship_details["max_modules"]
                equipment_type_key = "modules"
            else:
                # Unknown type — no slot restriction, allow it
                return compatibility

            # Use actual equipped count from the PlayerShip object when available;
            # otherwise fall back to global inventory count (deprecated path).
            if player_ship is not None:
                current_count = player_ship.get_equipped_count(equipment_type_key)
            else:
                # Fallback: query global inventory count (inaccurate for slot checks)
                inventory_type_map = {
                    "weapon": "primary_weapon",
                    "primary_weapon": "primary_weapon",
                    "secondary_weapon": "secondary_weapon",
                    "turret": "turret_weapon",
                    "turret_weapon": "turret_weapon",
                    "module": "module",
                }
                inventory_type = inventory_type_map.get(item_type_lower, item_type_lower)
                current_count = await self.inventory_repo.get_item_count_by_type(
                    db, player_id, inventory_type
                )

            if current_count >= max_slots:
                compatibility["compatible"] = False
                compatibility["reason"] = (
                    f"No available {item_type} slots on {ship_name} "
                    f"({current_count}/{max_slots} used)"
                )

            return compatibility

        except Exception as e:
            flogger.error(f"Error validating item compatibility: {e}")
            raise

    async def _get_item_details(self, db: AsyncSession, item_name: str) -> dict[str, Any] | None:
        """Get item details by searching all item repositories."""
        # Search primary weapons
        item = await self.primary_weapon_repo.get_by_name(db, item_name)
        if item:
            return {
                "name": item.name,
                "tech_level": getattr(item, "tech_level", None),
                "value": getattr(item, "value", None),
                "type": "primary_weapon",
            }

        # Search secondary weapons
        item = await self.secondary_weapon_repo.get_by_name(db, item_name)
        if item:
            return {
                "name": item.name,
                "tech_level": getattr(item, "tech_level", None),
                "value": getattr(item, "value", None),
                "type": "secondary_weapon",
            }

        # Search turret weapons
        item = await self.turret_weapon_repo.get_by_name(db, item_name)
        if item:
            return {
                "name": item.name,
                "tech_level": getattr(item, "tech_level", None),
                "value": getattr(item, "value", None),
                "type": "turret_weapon",
            }

        # Search modules
        item = await self.module_repo.get_by_name(db, item_name)
        if item:
            return {
                "name": item.name,
                "tech_level": getattr(item, "tech_level", None),
                "value": getattr(item, "value", None),
                "type": "module",
            }

        # Search ships
        item = await self.ship_repo.get_by_name(db, item_name)
        if item:
            return {
                "name": item.name,
                "tech_level": None,
                "value": getattr(item, "value", None),
                "type": "ship",
            }

        return None

    async def _get_ship_details(self, db: AsyncSession, ship_name: str) -> dict[str, Any] | None:
        """Get ship details from the database."""
        ship = await self.ship_repo.get_by_name(db, ship_name)
        if not ship:
            return None
        return {
            "name": ship.name,
            "max_primaries": ship.max_primaries,
            "max_modules": ship.max_modules,
            "max_secondaries": ship.max_secondaries,
            "max_turrets": ship.max_turrets,
            "value": getattr(ship, "value", None),
        }

    async def _validate_item_exists(
        self,
        db: AsyncSession,
        item_name: str,
        item_type: str,  # pylint: disable=unused-argument
    ) -> bool:
        """Validate that an item exists in the database across all item repositories."""
        repos = [
            self.ship_repo,
            self.primary_weapon_repo,
            self.secondary_weapon_repo,
            self.turret_weapon_repo,
            self.module_repo,
        ]
        for repo in repos:
            if await repo.get_by_name(db, item_name):
                return True
        return False

    async def get_player_item_count(
        self,
        db: AsyncSession,
        player_id: int,
        item_type: str,
        item_name: str
    ) -> int:
        """Get the quantity of a specific item a player owns."""
        try:
            item = await self.inventory_repo.get_player_item(db, player_id, item_type, item_name)
            return item.quantity if item else 0
        except Exception as e:
            flogger.error(f"Error getting item count for player {player_id}: {e}")
            raise

    async def consolidate_inventory(self, db: AsyncSession, player_id: int) -> dict[str, Any]:
        """
        Consolidate duplicate inventory entries (maintenance function).

        Groups items by (item_type, item_name), keeps one entry per group with
        the summed quantity, and deletes the rest.

        Returns consolidation results.
        """
        try:
            all_items = await self.inventory_repo.get_player_items(db, player_id)

            # Group items by (item_type, item_name)
            groups: dict[tuple[str, str], list] = {}
            for item in all_items:
                key = (item.item_type, item.item_name)
                groups.setdefault(key, []).append(item)

            items_consolidated = 0
            for (_itype, _iname), group in groups.items():
                if len(group) <= 1:
                    continue

                # Keep the first entry, merge all others into it
                primary = group[0]
                total_quantity = sum(i.quantity for i in group)

                # Delete all duplicate entries (all but the primary)
                for duplicate in group[1:]:
                    await self.inventory_repo.remove(db, duplicate)
                    items_consolidated += 1

                # Update the primary with the summed quantity
                await self.inventory_repo.update_quantity(db, primary.id, total_quantity)

            message = (
                f"Consolidated {items_consolidated} duplicate item(s)"
                if items_consolidated > 0
                else "Inventory is already consolidated"
            )

            return {
                "player_id": player_id,
                "items_consolidated": items_consolidated,
                "message": message,
            }

        except Exception as e:
            flogger.error(f"Error consolidating inventory for player {player_id}: {e}")
            raise
