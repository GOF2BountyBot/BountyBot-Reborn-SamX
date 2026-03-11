"""
Inventory Service for the BountyBot inventory system.

Handles business logic for inventory management including
item storage, quantity tracking, and inventory operations.
"""

from typing import Any

from persist.repositories.inventory_repository import InventoryRepository
from persist.repositories.player_repository import PlayerRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

flogger = bblogger.get_logger("inventory-service")

class InventoryService:
    def __init__(self):
        self.inventory_repo = InventoryRepository()
        self.player_repo = PlayerRepository()

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
                    # TODO: Add static item data integration
                    "item_details": await self._get_item_details(item.item_name)
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

            # TODO: Validate item exists in static data
            if not await self._validate_item_exists(item_name, item_type):
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
        db: AsyncSession,  # pylint: disable=unused-argument
        player_id: int,  # pylint: disable=unused-argument
        ship_name: str,
        item_type: str,
        item_name: str
    ) -> dict[str, Any]:
        """
        Validate if an item can be equipped on a specific ship.
        Returns compatibility information.
        """
        try:
            # TODO: Integrate with static ship/item data for compatibility checking
            # For now, return basic validation

            compatibility = {
                "compatible": True,  # Placeholder
                "ship_name": ship_name,
                "item_type": item_type,
                "item_name": item_name,
                "reason": None
            }

            # Basic validation - items must match ship's accepted types
            ship_details = await self._get_ship_details(ship_name)
            if not ship_details:
                compatibility["compatible"] = False
                compatibility["reason"] = f"Ship {ship_name} not found in database"

            return compatibility

        except Exception as e:
            flogger.error(f"Error validating item compatibility: {e}")
            raise

    async def _get_item_details(self, item_name: str) -> dict[str, Any]:
        """Get item details from static data (placeholder for integration)."""
        # TODO: Integrate with existing static data system
        return {
            "name": item_name,
            "description": f"Details for {item_name}",
            "tech_level": 1,  # Placeholder
            "value": 100      # Placeholder
        }

    async def _get_ship_details(self, ship_name: str) -> dict[str, Any] | None:
        """Get ship details from static data (placeholder for integration)."""
        # TODO: Integrate with existing static data system
        return {
            "name": ship_name,
            "max_weapons": 2,
            "max_modules": 3,
            "max_turrets": 1
        }

    async def _validate_item_exists(
        self,
        item_name: str,  # pylint: disable=unused-argument
        item_type: str  # pylint: disable=unused-argument
    ) -> bool:
        """Validate that an item exists in static data."""
        # TODO: Integrate with existing static data validation
        # For now, accept all items
        return True

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

    async def consolidate_inventory(self, _db: AsyncSession, player_id: int) -> dict[str, Any]:
        """
        Consolidate duplicate inventory entries (maintenance function).
        Returns consolidation results.
        """
        try:
            # This would be used if there are ever duplicate entries that need merging
            # For now, return success since our system prevents duplicates

            return {
                "player_id": player_id,
                "items_consolidated": 0,
                "message": "Inventory is already consolidated"
            }

        except Exception as e:
            flogger.error(f"Error consolidating inventory for player {player_id}: {e}")
            raise
