"""
Inventory repository for the BountyBot inventory system.

Handles database operations for PlayerInventory entities including
item management, quantity tracking, and inventory queries.
"""


from shared import bblogger
from sqlalchemy import and_, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from persist.interfaces.repository_interface import IRepository
from persist.models.player_inventory import PlayerInventory

flogger = bblogger.get_logger("inventory-repository")

class InventoryRepository(IRepository[PlayerInventory]):

    async def get_by_id(self, db: AsyncSession, obj_id: int) -> PlayerInventory | None:
        """Get inventory item by ID."""
        try:
            return await db.get(PlayerInventory, obj_id)
        except Exception as e:
            flogger.error(f"Error getting inventory item by ID {obj_id}: {e}")
            raise

    async def get_by_name(self, db: AsyncSession, name: str) -> PlayerInventory | None:
        """Not applicable for inventory items."""
        raise NotImplementedError("Inventory items don't have searchable names")

    async def list_all(self, db: AsyncSession) -> list[PlayerInventory]:
        """Get all inventory items."""
        try:
            result = await db.execute(select(PlayerInventory))
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error listing all inventory items: {e}")
            raise

    async def add(self, db: AsyncSession, obj: PlayerInventory) -> PlayerInventory:
        """Add new inventory item to database."""
        try:
            db.add(obj)
            await db.commit()
            await db.refresh(obj)
            flogger.info(f"Added inventory item: {obj.item_name} for player {obj.player_id}")
            return obj
        except Exception as e:
            flogger.error(f"Error adding inventory item: {e}")
            await db.rollback()
            raise

    async def create_or_update(self, db: AsyncSession, raw: dict) -> PlayerInventory:
        """Create or update inventory item from raw data."""
        try:
            player_id = raw.get("player_id")
            item_type = raw.get("item_type")
            item_name = raw.get("item_name")
            quantity = raw.get("quantity", 1)

            if not all([player_id, item_type, item_name]):
                raise ValueError("player_id, item_type, and item_name are required")

            # Check if item already exists
            existing_item = await self.get_player_item(db, player_id, item_type, item_name)

            if existing_item:
                # Update existing item quantity
                existing_item.quantity += quantity
                await db.commit()
                await db.refresh(existing_item)
                flogger.debug(f"Updated inventory item quantity: {item_name} for player {player_id}")
                return existing_item

            # Create new inventory item
            inventory_item = PlayerInventory(**raw)
            return await self.add(db, inventory_item)

        except Exception as e:
            flogger.error(f"Error creating/updating inventory item: {e}")
            raise

    async def remove(self, db: AsyncSession, obj: PlayerInventory) -> None:
        """Remove inventory item from database."""
        try:
            await db.delete(obj)
            await db.commit()
            flogger.info(f"Removed inventory item: {obj.item_name}")
        except Exception as e:
            flogger.error(f"Error removing inventory item: {e}")
            await db.rollback()
            raise

    async def get_player_items(
        self,
        db: AsyncSession,
        player_id: int,
        item_type: str | None = None
    ) -> list[PlayerInventory]:
        """Get all inventory items for a player, optionally filtered by type."""
        try:
            query = select(PlayerInventory).where(PlayerInventory.player_id == player_id)

            if item_type:
                query = query.where(PlayerInventory.item_type == item_type)

            result = await db.execute(query.order_by(PlayerInventory.item_type, PlayerInventory.item_name))
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error getting items for player {player_id}: {e}")
            raise

    async def get_player_item(
        self,
        db: AsyncSession,
        player_id: int,
        item_type: str,
        item_name: str
    ) -> PlayerInventory | None:
        """Get a specific item from player's inventory."""
        try:
            result = await db.execute(
                select(PlayerInventory).where(
                    and_(
                        PlayerInventory.player_id == player_id,
                        PlayerInventory.item_type == item_type,
                        PlayerInventory.item_name == item_name
                    )
                )
            )
            return result.scalars().first()
        except Exception as e:
            flogger.error(f"Error getting item {item_name} for player {player_id}: {e}")
            raise

    async def add_item(
        self,
        db: AsyncSession,
        player_id: int,
        item_type: str,
        item_name: str,
        quantity: int
    ) -> PlayerInventory:
        """Add items to player's inventory (or increase existing quantity)."""
        try:
            if quantity <= 0:
                raise ValueError("Quantity must be positive")

            existing_item = await self.get_player_item(db, player_id, item_type, item_name)

            if existing_item:
                # Update existing item
                new_quantity = existing_item.quantity + quantity
                await self.update_quantity(db, existing_item.id, new_quantity)
                await db.refresh(existing_item)
                return existing_item

            # Create new item
            item_data = {
                "player_id": player_id,
                "item_type": item_type,
                "item_name": item_name,
                "quantity": quantity
            }
            return await self.create_or_update(db, item_data)

        except Exception as e:
            flogger.error(f"Error adding item {item_name} to player {player_id}: {e}")
            raise

    async def remove_item(
        self,
        db: AsyncSession,
        player_id: int,
        item_type: str,
        item_name: str,
        quantity: int
    ) -> None:
        """Remove items from player's inventory."""
        try:
            if quantity <= 0:
                raise ValueError("Quantity must be positive")

            item = await self.get_player_item(db, player_id, item_type, item_name)
            if not item:
                raise ValueError(f"Item {item_name} not found in player inventory")

            if item.quantity < quantity:
                raise ValueError(f"Insufficient quantity. Available: {item.quantity}, Requested: {quantity}")

            new_quantity = item.quantity - quantity

            if new_quantity <= 0:
                # Remove item entirely
                await self.remove(db, item)
            else:
                # Update quantity
                await self.update_quantity(db, item.id, new_quantity)

            flogger.debug(f"Removed {quantity}x {item_name} from player {player_id}")

        except Exception as e:
            flogger.error(f"Error removing item {item_name} from player {player_id}: {e}")
            raise

    async def update_quantity(self, db: AsyncSession, inventory_id: int, new_quantity: int) -> PlayerInventory:
        """Update the quantity of an inventory item."""
        try:
            if new_quantity < 0:
                raise ValueError("Quantity cannot be negative")

            await db.execute(
                update(PlayerInventory)
                .where(PlayerInventory.id == inventory_id)
                .values(quantity=new_quantity)
            )
            await db.commit()

            item = await self.get_by_id(db, inventory_id)
            flogger.debug(f"Updated inventory item {inventory_id} quantity: {new_quantity}")
            return item
        except Exception as e:
            flogger.error(f"Error updating quantity for inventory item {inventory_id}: {e}")
            await db.rollback()
            raise

    async def get_item_count_by_type(self, db: AsyncSession, player_id: int, item_type: str) -> int:
        """Get total count of items of a specific type for a player."""
        try:
            result = await db.execute(
                select(PlayerInventory).where(
                    and_(
                        PlayerInventory.player_id == player_id,
                        PlayerInventory.item_type == item_type
                    )
                )
            )
            items = result.scalars().all()
            return sum(item.quantity for item in items)
        except Exception as e:
            flogger.error(f"Error getting item count for player {player_id}, type {item_type}: {e}")
            raise

    async def clear_player_inventory(self, db: AsyncSession, player_id: int) -> int:
        """Delete all inventory items for a player (used during prestige reset).

        Returns the number of rows deleted.
        """
        try:
            result = await db.execute(
                delete(PlayerInventory).where(PlayerInventory.player_id == player_id)
            )
            await db.flush()
            deleted_count = result.rowcount
            flogger.info(f"Cleared {deleted_count} inventory items for player {player_id}")
            return deleted_count
        except Exception as e:
            flogger.error(f"Error clearing inventory for player {player_id}: {e}")
            raise

    async def get_inventory_summary(self, db: AsyncSession, player_id: int) -> dict:
        """Get a summary of player's inventory by item type."""
        try:
            items = await self.get_player_items(db, player_id)

            summary = {
                "ship": 0,
                "weapon": 0,
                "module": 0,
                "turret": 0,
                "total_items": 0
            }

            for item in items:
                if item.item_type in summary:
                    summary[item.item_type] += item.quantity
                summary["total_items"] += item.quantity

            return summary
        except Exception as e:
            flogger.error(f"Error getting inventory summary for player {player_id}: {e}")
            raise
